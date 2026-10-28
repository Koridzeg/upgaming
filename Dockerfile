# One image: the Angular UI as static files, served by the FastAPI backend.
# Built and run by Render (render.yaml); runs anywhere:
#   docker build -t investigator .
#   docker run -p 7860:7860 --env-file .env investigator     # then http://localhost:7860
# Three stages, so build tools (Node, onnx) never reach the final image.

# --- 1. The UI: Angular build -> static files ---------------------------------------------
FROM node:22-slim AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx ng build

# --- 2. The embedding model: int8 file made from the pinned upstream model ------------------
# The same versions as development (constraints.txt) produce the same bytes, and the build
# fails if they don't: the database holds vectors from exactly this model (embedding_model).
FROM python:3.13-slim AS model
WORKDIR /app
COPY backend/pyproject.toml backend/constraints.txt backend/
COPY backend/investigator backend/investigator
RUN pip install --no-cache-dir -c backend/constraints.txt -e "./backend[data]"
RUN python -m investigator.embedding \
 && echo "6c09b0019d2f72058c72f39ca5b4f19e79edd65dbe944921c7395b74ddaeec43  data/models/bge-small-en-v1.5-5c38ec7-int8.onnx" \
    | sha256sum -c -

# --- 3. The app ----------------------------------------------------------------------------
FROM python:3.13-slim
# Not root: the app only writes under /app.
RUN useradd --create-home --uid 1000 user
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface
COPY backend/pyproject.toml backend/constraints.txt backend/
COPY backend/investigator backend/investigator
COPY backend/migrations backend/migrations
# Editable install: the code stays in /app/backend, where config.py finds data/ and frontend/.
RUN pip install -c backend/constraints.txt -e ./backend
COPY --from=model /app/data/models data/models
COPY --from=ui /src/frontend/dist frontend/dist
# The tokenizer is fetched now, so the app starts without downloading anything.
RUN python -c "from investigator.ingestion.tokenizer import get_tokenizer; get_tokenizer()" \
 && chown -R user /app
USER user
EXPOSE 7860
# Hosts like Render say which port to listen on in $PORT. `exec` makes uvicorn PID 1, so it
# receives the stop signal directly and shuts down cleanly (closing the database pool).
CMD ["sh", "-c", "exec uvicorn investigator.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
