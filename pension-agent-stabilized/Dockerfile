FROM python:3.12.13-slim

WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
ENV PENSION_AGENT_MODE=langgraph \
    PENSION_HOST=0.0.0.0 \
    PENSION_PORT=8000 \
    LAW_QUERY_FALLBACK_API=0 \
    PENSION_DISABLE_ENV_PROXY=1

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "web_app:app", "--host", "0.0.0.0", "--port", "8000"]
