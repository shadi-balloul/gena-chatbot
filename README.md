# BEMO Bank Chatbot

This project is an AI chatbot for BEMO Bank built using FastAPI, Gemini Models via the `google-genai` SDK, and MongoDB for storage. It uses context caching to load the bank information (from a PDF) once at startup, and supports chat sessions that are managed in memory with per‑user limits.

## Features
- **Context Caching:** Load the bank information once and reuse it across sessions.
- **Chat Sessions:** Each user can have one active chat session with configurable request and duration limits.
- **Token Counting:** Tracks input and output token counts using Gemini API responses.
- **Asynchronous Processing:** All Gemini and MongoDB operations are handled asynchronously.

## Setup
1. Clone the repository.
2. Create a virtual environment and install the packages:
   ```bash
   pip install -r requirements.txt
