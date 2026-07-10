# Taomly — Architecture Diagram

## System Overview

```mermaid
graph TD
    subgraph Clients
        A[Agency Admin<br/>agency_admin.html]
        B[Restaurant Admin<br/>admin.html]
        C[End User<br/>Telegram Mini App / PWA]
    end

    subgraph FastAPI Backend
        API[api.py<br/>CORS · Rate Limiting · Sentry]

        subgraph Routers
            R1[/api/agency]
            R2[/api/restaurants]
            R3[/api/menu]
            R4[/api/orders]
            R5[/api/analytics]
            R6[/api/billing]
            R7[/api/ai]
            R8[/api/reservations]
            R9[/api/waiter-calls]
            R10[/webhook/slug]
        end

        AUTH[auth.py<br/>JWT · Fernet · HMAC-SHA256]
        CONFIG[config.py<br/>Settings · Validation]
        AI[ai_service.py<br/>OpenRouter · OpenAI<br/>Anthropic · Gemini]
        TG[telegram_service.py<br/>Webhook Registration<br/>Order Notifications]
    end

    subgraph Data
        DB[(PostgreSQL<br/>Neon)]
        ALEMBIC[Alembic Migrations]
    end

    subgraph External Services
        BOT[Telegram Bot API<br/>per restaurant]
        SENTRY[Sentry]
        AIPROV[AI Provider<br/>OpenRouter / OpenAI]
    end

    A -->|HTTPS| API
    B -->|HTTPS| API
    C -->|HTTPS| API
    BOT -->|Webhook POST| R10

    API --> AUTH
    API --> CONFIG
    API --> R1 & R2 & R3 & R4 & R5 & R6 & R7 & R8 & R9 & R10

    R1 & R2 & R3 & R4 & R5 & R6 & R7 & R8 & R9 --> DB
    R7 --> AI
    R4 & R10 --> TG
    AI --> AIPROV
    TG --> BOT
    API --> SENTRY
    ALEMBIC --> DB
```

---

## Multi-Tenant Architecture

```mermaid
graph TD
    AGENCY[Agency<br/>Taomly Platform Owner]
    R1[Restaurant A<br/>slug: chinar]
    R2[Restaurant B<br/>slug: pizza_house]
    R3[Restaurant C<br/>slug: ...]

    U1[Users of A]
    U2[Users of B]
    U3[Users of C]

    BOT1[Bot @chinar_bot]
    BOT2[Bot @pizza_bot]
    BOT3[Bot @...]

    AGENCY -->|manages| R1
    AGENCY -->|manages| R2
    AGENCY -->|manages| R3

    R1 --> U1
    R2 --> U2
    R3 --> U3

    R1 --- BOT1
    R2 --- BOT2
    R3 --- BOT3
```

---

## Authentication Flow

```mermaid
sequenceDiagram
    participant U as End User
    participant TG as Telegram
    participant API as FastAPI
    participant DB as PostgreSQL

    U->>TG: Opens Mini App
    TG->>API: POST /api/restaurants/{slug}/auth<br/>with initData
    API->>API: HMAC-SHA256 verify initData<br/>using restaurant bot token
    API->>DB: Lookup restaurant by slug
    API-->>U: JWT token (restaurant-scoped)

    U->>API: Subsequent requests<br/>Authorization: Bearer token
    API->>API: Decode JWT → restaurant_id
    API->>DB: Query scoped to restaurant_id
    API-->>U: Response
```

---

## Database Schema (simplified)

```mermaid
erDiagram
    Agency ||--o{ Restaurant : manages
    Restaurant ||--o{ Category : has
    Restaurant ||--o{ Product : has
    Category ||--o{ Product : contains
    Restaurant ||--o{ Order : receives
    Order ||--o{ OrderItem : contains
    Product ||--o{ OrderItem : referenced_by
    Restaurant ||--o{ WaiterCall : has
    Restaurant ||--o{ Reservation : has
    Restaurant ||--o{ Subscription : has
    SubscriptionPlan ||--o{ Subscription : defines
    Subscription ||--o{ UsageEvent : tracks
```
