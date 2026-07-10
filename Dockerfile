FROM python:3.12-slim

# Scrapling 0.4.10 importa curl_cffi/playwright/browserforge però no els declara
# com a dependències — s'han d'instal·lar explícitament. (playwright: només el
# paquet Python, no calen navegadors perquè fem servir el fetcher HTTP estàtic.)
RUN pip install --no-cache-dir \
    scrapling==0.4.10 \
    curl_cffi \
    playwright \
    browserforge

WORKDIR /app
COPY login.py extract.py reasons.py scrape_pricelabs.py push_to_supabase.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

# RUN_AT: hora diària d'execució en UTC, format HH:MM (buit = executa un cop i surt)
ENV RUN_AT=""
ENV SCRAPE_DAYS=360

ENTRYPOINT ["./entrypoint.sh"]
