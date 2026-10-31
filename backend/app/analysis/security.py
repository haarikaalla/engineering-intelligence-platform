"""Security intelligence: secrets, injection, auth weaknesses, misconfiguration and dependency vulnerabilities."""
from __future__ import annotations

import bisect
import logging
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
I, M = re.I, re.M

SEVERITY_WEIGHT = {"critical": 15, "high": 8, "medium": 3, "low": 1, "info": 0}


@dataclass
class Rule:
    id: str
    title: str
    category: str  # secret | injection | auth | crypto | misconfig | xss | deserialization | transport
    severity: str
    cwe: str
    pattern: re.Pattern
    recommendation: str
    languages: frozenset[str] | None = None  # None = any
    file_filter: re.Pattern | None = None
    secret_group: int | None = None


@dataclass
class Finding:
    rule_id: str
    title: str
    category: str
    severity: str
    cwe: str
    file: str
    line: int
    snippet: str
    recommendation: str
    confidence: str = "high"


def _L(*langs: str) -> frozenset[str]:
    return frozenset(langs)


PY, JAVA, CS, JS, GO, PHP = _L("Python"), _L("Java", "Kotlin", "Groovy", "Scala"), _L("C#"), _L("JavaScript", "TypeScript", "Vue", "Svelte"), _L("Go"), _L("PHP")
CONFIG_FILES = re.compile(r"\.(properties|ya?ml|env|ini|cfg|conf|toml|json|xml)$|(^|/)\.env[^/]*$", I)
DOCKERFILE = re.compile(r"(^|/)Dockerfile[^/]*$", I)
K8S_FILES = re.compile(r"\.(ya?ml)$", I)
TF_FILES = re.compile(r"\.tf$")

RULES: list[Rule] = [
    # ---------------------------------------------------------------- secrets
    Rule("SEC001", "AWS access key ID", "secret", "critical", "CWE-798", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"), "Revoke the key in IAM and load credentials from a secrets manager / instance role.", secret_group=1),
    Rule("SEC002", "GitHub token", "secret", "critical", "CWE-798", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"), "Revoke the token and use GitHub Actions secrets or a vault.", secret_group=1),
    Rule("SEC003", "Private key material", "secret", "critical", "CWE-321", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"), "Remove the key from source control, rotate it and mount it from a secret store."),
    Rule("SEC004", "Stripe live secret key", "secret", "critical", "CWE-798", re.compile(r"\b(sk_live_[0-9a-zA-Z]{24,})\b"), "Rotate the key in Stripe dashboard and inject via environment.", secret_group=1),
    Rule("SEC005", "Slack token", "secret", "high", "CWE-798", re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"), "Revoke the Slack token and store it in a secret manager.", secret_group=1),
    Rule("SEC006", "Google API key", "secret", "high", "CWE-798", re.compile(r"\b(AIza[0-9A-Za-z\-_]{35})\b"), "Restrict and rotate the API key; load from configuration.", secret_group=1),
    Rule("SEC007", "Azure storage account key", "secret", "critical", "CWE-798", re.compile(r"AccountKey=([A-Za-z0-9+/=]{40,})"), "Rotate the storage key and use Managed Identity / Key Vault.", secret_group=1),
    Rule("SEC008", "Hardcoded credential", "secret", "high", "CWE-798",
         re.compile(r"""\b(?:password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key)\w*\b["']?\s*[:=]\s*["']([^"'\s]{6,})["']""", I),
         "Move the credential to environment variables or a secrets manager and rotate it.", secret_group=1),
    Rule("SEC009", "Credential in configuration file", "secret", "high", "CWE-260",
         re.compile(r"""^\s*[\w.\-]*(?:password|secret|api[-_.]?key|token|credentials?)[\w.\-]*\s*[=:]\s*["']?([^\s"'${}#]{6,})["']?\s*$""", I | M),
         "Reference a secret (e.g. ${DB_PASSWORD}) instead of a literal value.", file_filter=CONFIG_FILES, secret_group=1),
    Rule("SEC010", "Database URL with embedded password", "secret", "high", "CWE-798",
         re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)://[^:\s/'\"]+:([^@\s'\"]{3,})@"), "Remove credentials from connection URLs; use a secret store.", secret_group=1),
    Rule("SEC011", "Password in connection string", "secret", "high", "CWE-798", re.compile(r"(?:Password|Pwd)\s*=\s*([^;'\"\s]{4,})\s*;", I), "Use integrated auth / Managed Identity or a secret store for connection strings.", secret_group=1),
    Rule("SEC012", "JSON Web Token literal", "secret", "medium", "CWE-798", re.compile(r"\b(eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"), "Do not commit tokens; generate them at runtime.", secret_group=1),
    # ---------------------------------------------------------------- injection
    Rule("INJ001", "SQL built with f-string / formatting", "injection", "high", "CWE-89", re.compile(r"\.(?:execute|executemany|raw|text)\(\s*(?:f[\"']|[\"'][^\"']*[\"']\s*(?:%|\+|\.format\())"), "Use parameterised queries (cursor.execute(sql, params)) or ORM bindings.", PY),
    Rule("INJ002", "SQL built with string concatenation", "injection", "high", "CWE-89",
         re.compile(r"\b(?:createQuery|createNativeQuery|executeQuery|executeUpdate|prepareStatement|execute|queryForObject|queryForList|query|update)\s*\(\s*\"[^\"]*\"\s*\+\s*\w", ), "Use PreparedStatement placeholders or JPA named parameters.", JAVA),
    Rule("INJ003", "SQL string concatenated with variable", "injection", "medium", "CWE-89",
         re.compile(r"\"\s*(?:SELECT|INSERT|UPDATE|DELETE)\b[^\"]*(?:WHERE|VALUES|SET)[^\"]*\"\s*\+\s*[A-Za-z_]", I), "Build SQL with bind parameters instead of concatenation.", JAVA | CS),
    Rule("INJ004", "Interpolated SQL command", "injection", "high", "CWE-89",
         re.compile(r"(?:new\s+SqlCommand\(\s*(?:\$\"|\"[^\"]*\"\s*\+)|(?:FromSqlRaw|ExecuteSqlRaw|ExecuteSqlCommand)\(\s*(?:\$\"|\"[^\"]*\"\s*\+))"), "Use SqlParameter / FromSqlInterpolated.", CS),
    Rule("INJ005", "SQL template literal with interpolation", "injection", "high", "CWE-89",
         re.compile(r"\.(?:query|execute|raw|\$queryRawUnsafe|\$executeRawUnsafe)\(\s*(?:`[^`]*\$\{|[\"'][^\"']*[\"']\s*\+\s*\w)"), "Use parameter placeholders ($1, ?) or tagged templates (Prisma.sql).", JS),
    Rule("INJ006", "SQL built with fmt.Sprintf", "injection", "high", "CWE-89", re.compile(r"\.(?:Query|Exec|QueryRow|QueryContext|ExecContext)\(\s*(?:\w+,\s*)?(?:fmt\.Sprintf\(|\"[^\"]*\"\s*\+)"), "Use placeholders (?, $1) with args.", GO),
    Rule("INJ007", "Unsanitised request data in SQL", "injection", "critical", "CWE-89", re.compile(r"(?:mysqli?_query|->query)\([^)]*\$_(?:GET|POST|REQUEST|COOKIE)"), "Use PDO prepared statements.", PHP),
    Rule("INJ010", "OS command execution with shell", "injection", "high", "CWE-78", re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True|\bos\.system\(|\bos\.popen\("), "Pass argument lists without shell=True and validate input.", PY),
    Rule("INJ011", "Runtime.exec command execution", "injection", "medium", "CWE-78", re.compile(r"Runtime\.getRuntime\(\)\.exec\(|new\s+ProcessBuilder\("), "Avoid shell invocation; validate and allow-list arguments.", JAVA),
    Rule("INJ012", "child_process exec with dynamic input", "injection", "high", "CWE-78", re.compile(r"\bexec(?:Sync)?\(\s*(?:`[^`]*\$\{|[\"'][^\"']*[\"']\s*\+)"), "Use execFile/spawn with an argument array.", JS),
    Rule("INJ013", "Dynamic code evaluation", "injection", "high", "CWE-95", re.compile(r"(?<![\w.])eval\(|new\s+Function\("), "Remove eval; parse data with safe parsers (json, ast.literal_eval).", PY | JS),
    Rule("INJ014", "Path built from request input", "injection", "medium", "CWE-22", re.compile(r"(?:open|send_file|sendFile|readFile(?:Sync)?|new\s+File)\(\s*(?:request\.|req\.(?:query|params|body)|request\.getParameter)"), "Normalise and validate paths against an allow-listed base directory."),
    Rule("INJ015", "Server-side request to user-controlled URL", "injection", "medium", "CWE-918", re.compile(r"(?:requests\.(?:get|post)|fetch|axios\.(?:get|post)|httpx\.(?:get|post))\(\s*(?:request\.|req\.(?:query|params|body))"), "Validate outbound URLs against an allow-list."),
    # ---------------------------------------------------------------- deserialization / XSS
    Rule("DES001", "Unsafe deserialization", "deserialization", "high", "CWE-502",
         re.compile(r"pickle\.loads?\(|yaml\.load\((?![^)]*SafeLoader)|\bObjectInputStream\(|\bBinaryFormatter\b|TypeNameHandling\.(?:All|Auto|Objects)|\bXMLDecoder\("), "Use safe formats (JSON) or safe loaders; never deserialize untrusted data."),
    Rule("XSS001", "Raw HTML rendering", "xss", "medium", "CWE-79", re.compile(r"dangerouslySetInnerHTML|\.innerHTML\s*=|document\.write\(|\bv-html=|@Html\.Raw\(|\bmark_safe\(|\|\s*safe\b"), "Escape output or sanitise with DOMPurify / bleach before rendering."),
    # ---------------------------------------------------------------- auth / crypto / transport
    Rule("AUTH001", "CSRF protection disabled", "auth", "medium", "CWE-352", re.compile(r"csrf\(\)\s*\.disable\(\)|csrf\(\s*\w+\s*->\s*\w+\.disable\(\)\s*\)|csrf\(AbstractHttpConfigurer::disable\)|@csrf_exempt"), "Keep CSRF enabled for cookie-based sessions or use stateless tokens."),
    Rule("AUTH002", "All requests permitted", "auth", "high", "CWE-284", re.compile(r"anyRequest\(\)\s*\.permitAll\(\)|(?:antMatchers|requestMatchers|mvcMatchers)\(\s*\"/\*\*\"\s*\)\s*\.permitAll\(\)"), "Require authentication by default and allow-list public routes."),
    Rule("AUTH003", "JWT signature verification disabled", "auth", "critical", "CWE-347",
         re.compile(r"algorithms?\s*=\s*\[?\s*[\"']none[\"']|verify_signature[\"']?\s*:\s*False|jwt\.decode\([^)]*verify\s*=\s*False|ignoreExpiration\s*:\s*true|\.parseUnsecuredClaims\(", I), "Always verify JWT signature, algorithm and expiry."),
    Rule("AUTH004", "Hardcoded JWT signing key", "auth", "high", "CWE-321", re.compile(r"(?:setSigningKey|signWith|jwt\.sign|jwt\.encode|SymmetricSecurityKey\(Encoding\.\w+\.GetBytes)\([^)]*[\"'][A-Za-z0-9!@#$%^&*_\-]{8,}[\"']"), "Load signing keys from a secret store and rotate them."),
    Rule("AUTH005", "Anonymous access to endpoint", "auth", "low", "CWE-306", re.compile(r"@PermitAll\b|\[AllowAnonymous\]"), "Confirm the endpoint is intended to be public."),
    Rule("CRY001", "Weak hashing algorithm", "crypto", "medium", "CWE-327",
         re.compile(r"MessageDigest\.getInstance\(\s*\"(?:MD5|SHA-?1)\"|hashlib\.(?:md5|sha1)\(|createHash\(\s*[\"'](?:md5|sha1)[\"']|\b(?:MD5|SHA1)\.Create\(\)|DigestUtils\.(?:md5|sha1)", I), "Use SHA-256+ for integrity and bcrypt/Argon2/PBKDF2 for passwords."),
    Rule("CRY002", "Insecure cipher mode", "crypto", "medium", "CWE-327", re.compile(r"Cipher\.getInstance\(\s*\"(?:DES|RC4|AES/ECB|AES\")|\bDESCryptoServiceProvider\b|MODE_ECB"), "Use AES-GCM with random IVs."),
    Rule("TLS001", "TLS certificate validation disabled", "transport", "high", "CWE-295",
         re.compile(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*[\"']?0|InsecureSkipVerify\s*:\s*true|ServerCertificateValidationCallback\s*=[^;]*true|ServerCertificateCustomValidationCallback\s*=[^;]*true|TrustAllCerts|NoopHostnameVerifier"), "Keep certificate validation enabled; pin internal CAs instead."),
    Rule("TLS002", "Plain HTTP endpoint in configuration", "transport", "low", "CWE-319",
         re.compile(r"http://(?!localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|schemas\.|www\.w3\.org|maven\.apache\.org|java\.sun\.com|xmlns|json-schema\.org|www\.springframework\.org|host\.docker\.internal)[\w.\-]+\.[a-z]{2,}"),
         "Use HTTPS for remote endpoints.", file_filter=CONFIG_FILES),
    # ---------------------------------------------------------------- misconfiguration
    Rule("CFG001", "Debug mode enabled", "misconfig", "medium", "CWE-489", re.compile(r"^\s*DEBUG\s*=\s*True\b|app\.run\([^)]*debug\s*=\s*True", M), "Disable debug in production; drive it from environment configuration.", PY),
    Rule("CFG002", "Wildcard allowed hosts", "misconfig", "medium", "CWE-16", re.compile(r"ALLOWED_HOSTS\s*=\s*\[\s*[\"']\*[\"']"), "Restrict ALLOWED_HOSTS to known domains.", PY),
    Rule("CFG003", "Permissive CORS policy", "misconfig", "medium", "CWE-942",
         re.compile(r"Access-Control-Allow-Origin[\"']?\s*[:,]\s*[\"']\*|allow_origins\s*=\s*\[\s*[\"']\*[\"']\s*\]|@CrossOrigin(?:\s*$|\(\s*(?:origins\s*=\s*)?\"\*\"\s*\))|AllowAnyOrigin\(\)|origin\s*:\s*[\"']\*[\"']|allowedOrigins\(\s*\"\*\"\s*\)", M), "Allow-list trusted origins, especially when credentials are allowed."),
    Rule("CFG004", "Actuator endpoints fully exposed", "misconfig", "medium", "CWE-200", re.compile(r"management\.endpoints\.web\.exposure\.include\s*[=:]\s*[\"']?\*|include:\s*[\"']\*[\"']"), "Expose only health/info; protect the rest.", file_filter=CONFIG_FILES),
    Rule("CFG005", "H2 console enabled", "misconfig", "medium", "CWE-16", re.compile(r"spring\.h2\.console\.enabled\s*[=:]\s*true|h2\.console\.enabled\s*:\s*true"), "Disable the H2 console outside local development.", file_filter=CONFIG_FILES),
    Rule("CFG006", "Container privileged mode", "misconfig", "high", "CWE-250", re.compile(r"^\s*privileged:\s*true", M), "Remove privileged mode; grant specific capabilities only.", file_filter=K8S_FILES),
    Rule("CFG007", "Privilege escalation allowed", "misconfig", "medium", "CWE-250", re.compile(r"allowPrivilegeEscalation:\s*true|hostNetwork:\s*true|hostPID:\s*true|runAsUser:\s*0\b"), "Set allowPrivilegeEscalation: false and runAsNonRoot: true.", file_filter=K8S_FILES),
    Rule("CFG008", "Unpinned base image", "misconfig", "low", "CWE-1104", re.compile(r"^\s*FROM\s+(?:[^\s:@]+:latest|[^\s:@]+)(?:\s+AS\s+\w+)?\s*$", I | M), "Pin base images by version or digest.", file_filter=DOCKERFILE),
    Rule("CFG009", "Secret baked into image", "secret", "high", "CWE-538", re.compile(r"^\s*(?:ENV|ARG)\s+\w*(?:PASSWORD|SECRET|TOKEN|API_KEY)\w*[= ]\s*\S+", I | M), "Inject secrets at runtime (secrets mount / env from secret store).", file_filter=DOCKERFILE),
    Rule("CFG010", "Public cloud resource / open ingress", "misconfig", "high", "CWE-284", re.compile(r"acl\s*=\s*\"public-read(?:-write)?\"|cidr_blocks\s*=\s*\[\s*\"0\.0\.0\.0/0\"\s*\]|encrypted\s*=\s*false|publicly_accessible\s*=\s*true"), "Restrict network exposure and enable encryption.", file_filter=TF_FILES),
    Rule("CFG011", "Insecure cookie flags", "misconfig", "low", "CWE-614", re.compile(r"SESSION_COOKIE_SECURE\s*=\s*False|httpOnly\s*:\s*false|secure\s*:\s*false|CookieSecurePolicy\.None"), "Set Secure and HttpOnly on session cookies."),
    Rule("LOG001", "Sensitive data logged", "misconfig", "low", "CWE-532", re.compile(r"(?:log(?:ger)?|console)\.\w+\([^)]*\b(?:password|secret|token|creditCard|ssn)\b", I), "Never log credentials or personal data."),
]

_PLACEHOLDER = re.compile(r"example|changeme|change_me|your[_-]?|xxx|placeholder|dummy|sample|<|\$\{|\{\{|%\(|process\.env|os\.getenv|environ|\*\*\*|redacted|todo|secret_?key_?here|^test|^password$|^secret$|^none$|^null$|^true$|^false$", I)


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    return -sum(c / len(s) * math.log2(c / len(s)) for c in counts.values())


def _mask(value: str) -> str:
    return value[:4] + "*" * min(12, max(4, len(value) - 4))


def _secret_ok(rule: Rule, value: str) -> bool:
    if _PLACEHOLDER.search(value):
        return False
    if rule.id in ("SEC008", "SEC009", "SEC011", "SEC010"):
        if len(set(value)) < 4 or _entropy(value) < 2.5:
            return False
        if re.fullmatch(r"[a-z_.]+", value) and rule.id != "SEC010":
            return False  # property references / identifiers
    return True


def scan_text(path: str, language: str, text: str, category: str, line_offsets: dict[int, int] | None = None) -> list[Finding]:
    """Scan text. line_offsets maps local line -> original line (used for PR diffs)."""
    findings: list[Finding] = []
    lines = text.splitlines()
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)

    for rule in RULES:
        if rule.languages is not None and language not in rule.languages:
            continue
        if rule.file_filter is not None and not rule.file_filter.search(path):
            continue
        if rule.category != "secret" and category == "doc":
            continue
        for m in rule.pattern.finditer(text):
            line_no = bisect.bisect_right(starts, m.start())
            snippet_line = lines[line_no - 1] if 0 < line_no <= len(lines) else m.group(0)
            if re.match(r"^\s*(#|//|\*|/\*|<!--)", snippet_line) and rule.category not in ("secret",):
                continue
            snippet = snippet_line.strip()[:200]
            if rule.secret_group is not None:
                value = m.group(rule.secret_group)
                if not _secret_ok(rule, value):
                    continue
                snippet = snippet.replace(value, _mask(value))
            confidence = "high"
            if category in ("test", "doc") or re.search(r"(^|/)(tests?|fixtures?|examples?|samples?|mocks?)/", path, I):
                confidence = "low"
            findings.append(Finding(rule.id, rule.title, rule.category, rule.severity if confidence == "high" else _downgrade(rule.severity),
                                    rule.cwe, path, (line_offsets or {}).get(line_no, line_no), snippet, rule.recommendation, confidence))
    if DOCKERFILE.search(path) and not re.search(r"^\s*USER\s+(?!root\b|0\b)\S+", text, I | M):
        findings.append(Finding("CFG012", "Container runs as root", "misconfig", "medium", "CWE-250", path, 1,
                                "No non-root USER instruction", "Add a non-root USER to the Dockerfile."))
    return findings


def _downgrade(sev: str) -> str:
    return {"critical": "medium", "high": "low", "medium": "low"}.get(sev, sev)


# ---------------------------------------------------------------- dependencies
_KNOWN_VULNS: list[tuple[str, str, str, str, str, str]] = [
    ("Maven", "org.apache.logging.log4j:log4j-core", "2.17.1", "CVE-2021-44228", "critical", "Log4Shell remote code execution via JNDI lookups"),
    ("Maven", "org.springframework:spring-beans", "5.3.18", "CVE-2022-22965", "critical", "Spring4Shell RCE via data binding"),
    ("Maven", "org.springframework:spring-webmvc", "5.3.18", "CVE-2022-22965", "critical", "Spring4Shell RCE via data binding"),
    ("Maven", "org.springframework.boot:spring-boot-starter-parent", "2.6.6", "CVE-2022-22965", "critical", "Spring Boot < 2.6.6 ships Spring Framework vulnerable to Spring4Shell"),
    ("Maven", "com.fasterxml.jackson.core:jackson-databind", "2.13.4.2", "CVE-2022-42003", "high", "Deep wrapper array nesting DoS"),
    ("Maven", "org.apache.struts:struts2-core", "2.5.33", "CVE-2023-50164", "critical", "File upload path traversal leading to RCE"),
    ("Maven", "commons-collections:commons-collections", "3.2.2", "CVE-2015-7501", "critical", "Unsafe deserialization gadget chain"),
    ("Maven", "org.yaml:snakeyaml", "2.0", "CVE-2022-1471", "high", "Constructor deserialization RCE"),
    ("npm", "lodash", "4.17.21", "CVE-2021-23337", "high", "Command injection in template"),
    ("npm", "minimist", "1.2.6", "CVE-2021-44906", "critical", "Prototype pollution"),
    ("npm", "axios", "1.6.0", "CVE-2023-45857", "medium", "XSRF-TOKEN leakage"),
    ("npm", "jsonwebtoken", "9.0.0", "CVE-2022-23529", "high", "Insecure key handling"),
    ("npm", "express", "4.19.2", "CVE-2024-29041", "medium", "Open redirect in malformed URLs"),
    ("npm", "node-fetch", "2.6.7", "CVE-2022-0235", "high", "Exposure of sensitive information to unauthorized actor"),
    ("PyPI", "pyyaml", "5.4", "CVE-2020-14343", "critical", "Arbitrary code execution in full_load"),
    ("PyPI", "requests", "2.31.0", "CVE-2023-32681", "medium", "Proxy-Authorization header leak"),
    ("PyPI", "flask", "2.2.5", "CVE-2023-30861", "high", "Session cookie disclosure via caching proxy"),
    ("PyPI", "jinja2", "3.1.3", "CVE-2024-22195", "medium", "XSS via xmlattr filter"),
    ("PyPI", "urllib3", "1.26.18", "CVE-2023-45803", "medium", "Request body not stripped on redirect"),
    ("PyPI", "django", "3.2.25", "CVE-2024-27351", "medium", "ReDoS in Truncator.words"),
    ("NuGet", "Newtonsoft.Json", "13.0.1", "GHSA-5crp-9r3c-p9vr", "high", "Improper handling of exceptional conditions (DoS)"),
    ("NuGet", "System.Text.Encodings.Web", "4.5.1", "CVE-2021-26701", "critical", "Remote code execution"),
    ("Go", "github.com/gin-gonic/gin", "1.9.1", "CVE-2023-29401", "medium", "Content-Disposition header injection"),
]


def _ver(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


def _builtin_vulns(deps: list[dict]) -> list[dict]:
    out = []
    for d in deps:
        if not d.get("version"):
            continue
        for eco, name, fixed, vid, sev, summary in _KNOWN_VULNS:
            if d["ecosystem"] == eco and d["name"].lower() == name.lower() and _ver(d["version"]) < _ver(fixed):
                out.append({"package": d["name"], "version": d["version"], "ecosystem": eco, "id": vid, "severity": sev,
                            "summary": summary, "fixed_in": fixed, "file": d["file"], "source": "builtin"})
    return out


def _osv_severity(vuln: dict) -> str:
    sev = (vuln.get("database_specific") or {}).get("severity")
    if isinstance(sev, str) and sev.lower() in ("critical", "high", "medium", "moderate", "low"):
        return "medium" if sev.lower() == "moderate" else sev.lower()
    for s in vuln.get("severity") or []:
        m = re.search(r"CVSS:3\.\d/AV:(\w)", s.get("score", ""))
        if m:
            return "high"
    return "medium"


def _osv_fixed(vuln: dict, name: str) -> str | None:
    for aff in vuln.get("affected", []):
        if (aff.get("package") or {}).get("name", "").lower() != name.lower():
            continue
        for rng in aff.get("ranges", []):
            for ev in rng.get("events", []):
                if "fixed" in ev:
                    return ev["fixed"]
    return None


def dependency_vulnerabilities(deps: list[dict]) -> dict:
    pinned = [d for d in deps if d.get("version")]
    unique = list({(d["ecosystem"], d["name"], d["version"]): d for d in pinned}.values())
    settings = get_settings()
    vulns: list[dict] = []
    source = "builtin"
    if settings.enable_osv and unique:
        try:
            with httpx.Client(timeout=settings.osv_timeout_s) as client:
                ids_for: dict[int, list[str]] = {}
                for i in range(0, len(unique), 500):
                    batch = unique[i:i + 500]
                    resp = client.post("https://api.osv.dev/v1/querybatch", json={"queries": [
                        {"package": {"name": d["name"], "ecosystem": d["ecosystem"]}, "version": d["version"]} for d in batch]})
                    resp.raise_for_status()
                    for j, res in enumerate(resp.json().get("results", [])):
                        ids = [v["id"] for v in res.get("vulns", []) or []]
                        if ids:
                            ids_for[i + j] = ids
                details: dict[str, dict] = {}
                for vid in list({v for ids in ids_for.values() for v in ids})[:80]:
                    r = client.get(f"https://api.osv.dev/v1/vulns/{vid}")
                    if r.status_code == 200:
                        details[vid] = r.json()
                for idx, ids in ids_for.items():
                    d = unique[idx]
                    for vid in ids:
                        det = details.get(vid, {})
                        vulns.append({"package": d["name"], "version": d["version"], "ecosystem": d["ecosystem"],
                                      "id": vid, "aliases": det.get("aliases", [])[:3], "severity": _osv_severity(det) if det else "medium",
                                      "summary": (det.get("summary") or det.get("details", "")[:200]) if det else "See OSV advisory",
                                      "fixed_in": _osv_fixed(det, d["name"]) if det else None, "file": d["file"], "source": "osv.dev"})
                source = "osv.dev"
        except (httpx.HTTPError, ValueError) as exc:
            log.info("OSV lookup unavailable (%s); using built-in advisory list", exc)
    if source == "builtin":
        vulns = _builtin_vulns(unique)
    return {
        "source": source,
        "total_dependencies": len(deps),
        "pinned": len(pinned),
        "unpinned": len(deps) - len(pinned),
        "by_ecosystem": dict(Counter(d["ecosystem"] for d in deps)),
        "vulnerabilities": sorted(vulns, key=lambda v: -SEVERITY_WEIGHT.get(v["severity"], 0)),
    }


# ---------------------------------------------------------------- repository scan
def security_report(ctx) -> dict:
    findings: list[Finding] = []
    for f in ctx.files:
        if f.category == "other":
            continue
        text = ctx.read(f.path)
        if text:
            findings.extend(scan_text(f.path, f.language, text, f.category))
    deps = dependency_vulnerabilities(ctx.dependencies)
    return summarize(findings, deps, ctx)


def security_score(findings: list[dict], dep_vulns: list[dict], kloc: float) -> int:
    penalty = sum(SEVERITY_WEIGHT.get(f["severity"], 0) for f in findings if f.get("confidence") != "low")
    penalty += sum({"critical": 10, "high": 6, "medium": 2, "low": 1}.get(v["severity"], 1) for v in dep_vulns)
    return int(round(100 * math.exp(-penalty / (40 + 4 * math.sqrt(max(kloc, 1))))))


def summarize(findings: list[Finding], deps: dict, ctx) -> dict:
    rows = [asdict(f) for f in findings]
    rows.sort(key=lambda r: (-SEVERITY_WEIGHT.get(r["severity"], 0), r["file"], r["line"]))
    kloc = sum(f.loc for f in ctx.files if f.category == "source") / 1000
    by_file = Counter(r["file"] for r in rows if r["confidence"] == "high")
    auth_files = [p for p, pf in ctx.parsed.items() if re.search(r"auth|security|login|jwt|token|oauth|session|password", p, I)
                  and ctx.file_info[p].category == "source"]
    return {
        "score": security_score(rows, deps["vulnerabilities"], kloc),
        "summary": {
            "total": len(rows),
            "by_severity": dict(Counter(r["severity"] for r in rows)),
            "by_category": dict(Counter(r["category"] for r in rows)),
            "dependency_vulnerabilities": len(deps["vulnerabilities"]),
        },
        "findings": rows[:2000],
        "dependencies": deps,
        "risky_files": [{"file": f, "findings": c} for f, c in by_file.most_common(15)],
        "auth_surface": auth_files[:50],
    }
