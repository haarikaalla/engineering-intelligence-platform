"""A small multi-service 'enterprise' repository used by the test-suite."""
from pathlib import Path

# Fake credentials are assembled at runtime so secret scanners don't flag this repository.
FAKE_AWS_KEY = "AKIA" + "IOSFODNN7ABCDEFG"
FAKE_STRIPE_KEY = "sk_" + "live_" + "51HxQ2aBcDeFgHiJkLmNoPqRs"

SAMPLE_REPO: dict[str, str] = {
    "README.md": "# Acme Shop\n\nAcme Shop is an order management platform made of Spring Boot microservices and a Python notification service.\n",
    "docker-compose.yml": """
services:
  order-service:
    build: ./order-service
    depends_on: [postgres, kafka, payment-service]
  payment-service:
    build: ./payment-service
    depends_on: [postgres, kafka]
  notification-service:
    build: ./notification-service
  postgres:
    image: postgres:16
  kafka:
    image: bitnami/kafka:3.7
""",
    "order-service/pom.xml": """<project>
  <parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>2.5.0</version></parent>
  <properties><log4j.version>2.14.1</log4j.version></properties>
  <dependencies>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency>
    <dependency><groupId>org.springframework.kafka</groupId><artifactId>spring-kafka</artifactId></dependency>
    <dependency><groupId>org.apache.logging.log4j</groupId><artifactId>log4j-core</artifactId><version>${log4j.version}</version></dependency>
    <dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><scope>test</scope></dependency>
  </dependencies>
</project>
""",
    "order-service/Dockerfile": "FROM eclipse-temurin:17\nCOPY target/app.jar /app.jar\nENTRYPOINT [\"java\", \"-jar\", \"/app.jar\"]\n",
    "order-service/src/main/java/com/acme/order/OrderApplication.java": """package com.acme.order;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class OrderApplication {
    public static void main(String[] args) {
        SpringApplication.run(OrderApplication.class, args);
    }
}
""",
    "order-service/src/main/java/com/acme/order/controller/OrderController.java": """package com.acme.order.controller;

import com.acme.order.model.Order;
import com.acme.order.service.OrderService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class OrderController {
    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @GetMapping("/{id}")
    public Order getOrder(@PathVariable Long id) {
        return orderService.findOrder(id);
    }

    @PostMapping
    public Order createOrder(@RequestBody Order order) {
        if (order == null) {
            throw new IllegalArgumentException("order required");
        }
        return orderService.placeOrder(order);
    }
}
""",
    "order-service/src/main/java/com/acme/order/service/OrderService.java": """package com.acme.order.service;

import com.acme.order.client.PaymentClient;
import com.acme.order.model.Order;
import com.acme.order.repository.OrderRepository;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

@Service
public class OrderService {
    private final OrderRepository orderRepository;
    private final PaymentClient paymentClient;
    private final KafkaTemplate<String, String> kafkaTemplate;

    public OrderService(OrderRepository orderRepository, PaymentClient paymentClient, KafkaTemplate<String, String> kafkaTemplate) {
        this.orderRepository = orderRepository;
        this.paymentClient = paymentClient;
        this.kafkaTemplate = kafkaTemplate;
    }

    public Order findOrder(Long id) {
        return orderRepository.findById(id).orElseThrow();
    }

    public Order placeOrder(Order order) {
        Order saved = orderRepository.save(order);
        if (saved.getTotal() > 0 && paymentClient.charge(saved)) {
            kafkaTemplate.send("order-created", saved.getId().toString());
        }
        return saved;
    }
}
""",
    "order-service/src/main/java/com/acme/order/repository/OrderRepository.java": """package com.acme.order.repository;

import com.acme.order.model.Order;
import org.springframework.data.jpa.repository.JpaRepository;

public interface OrderRepository extends JpaRepository<Order, Long> {
}
""",
    "order-service/src/main/java/com/acme/order/repository/OrderSearchDao.java": """package com.acme.order.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import java.util.List;
import java.util.Map;

public class OrderSearchDao {
    private final JdbcTemplate jdbcTemplate;

    public OrderSearchDao(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public List<Map<String, Object>> search(String customer) {
        return jdbcTemplate.queryForList("SELECT * FROM orders WHERE customer = '" + customer + "'");
    }
}
""",
    "order-service/src/main/java/com/acme/order/model/Order.java": """package com.acme.order.model;

import jakarta.persistence.Entity;
import jakarta.persistence.Table;

@Entity
@Table(name = "orders")
public class Order {
    private Long id;
    private double total;
    public Long getId() { return id; }
    public double getTotal() { return total; }
}
""",
    "order-service/src/main/java/com/acme/order/client/PaymentClient.java": """package com.acme.order.client;

import com.acme.order.model.Order;
import org.springframework.web.client.RestTemplate;

public class PaymentClient {
    private final RestTemplate restTemplate = new RestTemplate();

    public boolean charge(Order order) {
        return restTemplate.postForObject("http://payment-service:8080/api/payments", order, Boolean.class);
    }
}
""",
    "order-service/src/main/resources/application.properties": "spring.datasource.url=jdbc:postgresql://postgres:5432/orders\nspring.datasource.password=SuperS3cretPassw0rd!\nmanagement.endpoints.web.exposure.include=*\n",
    "order-service/src/test/java/com/acme/order/service/OrderServiceTest.java": """package com.acme.order.service;

import org.junit.jupiter.api.Test;

class OrderServiceTest {
    @Test
    void placesOrder() {
        OrderService service = new OrderService(null, null, null);
    }
}
""",
    "payment-service/pom.xml": "<project><dependencies><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency></dependencies></project>\n",
    "payment-service/src/main/java/com/acme/payment/PaymentApplication.java": """package com.acme.payment;

@SpringBootApplication
public class PaymentApplication {
    public static void main(String[] args) { }
}
""",
    "payment-service/src/main/java/com/acme/payment/api/PaymentController.java": """package com.acme.payment.api;

import com.acme.payment.service.PaymentService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/payments")
public class PaymentController {
    private final PaymentService paymentService;

    public PaymentController(PaymentService paymentService) {
        this.paymentService = paymentService;
    }

    @PostMapping
    public boolean charge(@RequestBody String body) {
        return paymentService.charge(body);
    }
}
""",
    "payment-service/src/main/java/com/acme/payment/service/PaymentService.java": """package com.acme.payment.service;

import com.acme.payment.repository.PaymentRepository;
import java.security.MessageDigest;
import org.springframework.kafka.annotation.KafkaListener;

public class PaymentService {
    private final PaymentRepository paymentRepository;

    public PaymentService(PaymentRepository paymentRepository) {
        this.paymentRepository = paymentRepository;
    }

    public boolean charge(String body) {
        MessageDigest md = MessageDigest.getInstance("MD5");
        return body != null;
    }

    @KafkaListener(topics = "order-created")
    public void onOrderCreated(String orderId) {
        paymentRepository.count();
    }
}
""",
    "payment-service/src/main/java/com/acme/payment/repository/PaymentRepository.java": """package com.acme.payment.repository;

import com.acme.payment.model.Payment;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PaymentRepository extends JpaRepository<Payment, Long> {
}
""",
    "payment-service/src/main/java/com/acme/payment/model/Payment.java": """package com.acme.payment.model;

@Entity
@Table(name = "payments")
public class Payment {
    private Long id;
}
""",
    "notification-service/requirements.txt": "fastapi==0.110.0\npyyaml==5.3\n",
    "notification-service/Dockerfile": "FROM python:3.12-slim\nUSER app\n",
    "notification-service/app/__init__.py": "",
    "notification-service/app/main.py": """from fastapi import FastAPI

from app.services.notifier import send_notification
from app.auth import current_user

app = FastAPI()


@app.post("/notifications")
def notify(payload: dict):
    user = current_user(payload.get("token"))
    return send_notification(user, payload["message"])


@app.get("/notifications/{nid}")
def get_notification(nid: int):
    return {"id": nid}
""",
    "notification-service/app/services/__init__.py": "",
    "notification-service/app/services/notifier.py": """import sqlite3

AWS_ACCESS_KEY_ID = "__FAKE_AWS_KEY__"


def send_notification(user, message):
    conn = sqlite3.connect("notify.db")
    conn.execute(f"INSERT INTO notifications (user, message) VALUES ('{user}', '{message}')")
    return {"status": "sent"}
""",
    "notification-service/app/auth.py": """import jwt


def current_user(token):
    claims = jwt.decode(token, options={"verify_signature": False})
    return claims.get("sub")
""",
}


def write_sample_repo(root: Path) -> Path:
    for rel, content in SAMPLE_REPO.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content.replace("__FAKE_AWS_KEY__", FAKE_AWS_KEY), encoding="utf-8")
    return root
