# PartSelect Chat Agent - Setup Guide

An AI-powered chat agent for PartSelect that helps users find appliance parts and answers product questions.

## Prerequisites

- Node.js (v16 or higher)
- Python 3.9+
- pip

## Quick Start

### 1. Environment Setup

Copy the environment file (credentials are pre-configured):

```bash
cp .env.example .env
```

### 2. Install Dependencies

**Backend (Python):**
```bash
pip install -r requirements.txt
```

**Frontend (React):**
```bash
npm install
```

### 3. Run the Application

You need two terminal windows:

**Terminal 1 - Start the backend:**
```bash
python3 -m backend.main
```

**Terminal 2 - Start the frontend:**
```bash
npm start
```

The app will open at [http://localhost:3000](http://localhost:3000)

## Project Structure

```
├── backend/           # FastAPI backend with AI agent
│   ├── agent_v2/      # LangGraph-based chat agent
│   ├── db/            # Database utilities
│   └── main.py        # API endpoints
├── src/               # React frontend
│   ├── components/    # UI components
│   └── api/           # API client
├── scrapers/          # Data collection scripts
└── data/              # Scraped product data
```

## Tech Stack

- **Frontend:** React
- **Backend:** FastAPI + Uvicorn
- **AI:** Claude (Anthropic) + LangGraph
- **Database:** Supabase (PostgreSQL + vector search)
- **Embeddings:** sentence-transformers (local)
