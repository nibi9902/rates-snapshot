FROM python:3.12-slim

RUN pip install --no-cache-dir scrapling curl_cffi playwright

WORKDIR /app
COPY login.py extract.py scrape_pricelabs.py push_to_supabase.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

# RUN_AT: hora diària d'execució en UTC, format HH:MM (buit = executa un cop i surt)
ENV RUN_AT=""
ENV SCRAPE_DAYS=360

ENTRYPOINT ["./entrypoint.sh"]
