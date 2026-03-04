# Fraud Detection Application

A comprehensive fraud detection system built with FastAPI backend and Next.js frontend, utilizing Aerospike Graph for real-time graph-based fraud detection.

## 🚀 Quick Start

1. Copy the sample env file and add your Gemini API key:
```bash
cp .env.sample .env
```
Then edit `.env` and set your key:
```
GEMINI_API_KEY=your-gemini-api-key-here
```

2. Start all services:
```bash
docker compose up -d
```

**Access the application:**
- Frontend: http://localhost:8080

## 🏗️ Architecture

```
┌──────────────┐     ┌──────────────┐     ┌────────────────────┐     ┌───────────────┐
│   Frontend   │────▶│   Backend    │────▶│  Aerospike Graph   │────▶│  Aerospike DB │
│  :8080       │     │  :4000       │     │  Service :8182     │     │  :3000        │
└──────────────┘     └──────┬───────┘     └────────┬───────────┘     └───────────────┘
                            │                      │
                       ┌────▼─────┐          ┌─────▼──────┐
                       │  Gemini  │          │   Zipkin   │
                       │  API     │          │   :9411    │
                       └──────────┘          └────────────┘

┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Aerospike   │────▶│  Prometheus  │────▶│   Grafana    │
│  Exporter    │     │  :9091       │     │   :3030      │
└──────────────┘     └──────────────┘     └──────────────┘
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| **Frontend** | 8080 | Next.js dashboard for exploring users, transactions, and fraud patterns |
| **Backend** | 4000 | FastAPI server handling fraud detection logic and Gremlin queries |
| **Generator** | 4001 | Synthetic data generator for seeding the graph |
| **Aerospike DB** | 3000 | Key-value and graph data store |
| **Aerospike Graph Service** | 8182 | Gremlin-compatible graph query engine on top of Aerospike |
| **Zipkin** | 9411 | Distributed tracing for graph query performance |
| **Aerospike Exporter** | 9145 | Prometheus metrics exporter for Aerospike |
| **Prometheus** | 9091 | Metrics collection and storage |
| **Grafana** | 3030 | Monitoring dashboards (default login `admin`/`admin`) |


## 🕵️ Fraud Detection System

The system implements real-time fraud detection using graph-based analysis:

### RT1 - Flagged Account Detection
- **Purpose**: Detects transactions involving previously flagged accounts
- **Method**: 1-hop graph lookup for immediate threat detection
- **Risk Level**: High
- **Use Cases**: Known fraudster connections, blacklisted accounts

### RT2 - Flagged Device Connection  
- **Purpose**: Detects transactions involving accounts connected to flagged devices
- **Method**: Network analysis through transaction history
- **Risk Level**: High
- **Use Cases**: Device-based fraud networks, shared device abuse

### RT3 - Supernode Detection (Future)
- **Purpose**: Identifies accounts with unusually high connectivity
- **Method**: Graph centrality analysis
- **Risk Level**: Medium-High
- **Use Cases**: Money laundering hubs, distribution networks


## 📚 Documentation

- **[Data Model](./docs/datamodel.md)** - Detailed data structure documentation

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

