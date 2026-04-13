# carburanti-bot

Bot Telegram per monitorare i prezzi carburante in Italia in tempo reale, con dati ufficiali MIMIT/Osservaprezzi.

> Standalone, senza Home Assistant. Si avvia con Docker in un minuto.

## Indice

1. [Cosa fa](#cosa-fa)
2. [Comandi disponibili](#comandi-disponibili)
3. [Prerequisiti](#prerequisiti)
4. [Installazione locale (Python)](#installazione-locale-python)
5. [Installazione con Docker](#installazione-con-docker)
6. [Pipeline CI/CD immagine Docker](#pipeline-cicd-immagine-docker)
7. [Deploy con Portainer (Stack)](#deploy-con-portainer-stack)
8. [Persistenza dati](#persistenza-dati)
9. [Come trovare l'ID stazione](#come-trovare-lid-stazione)
10. [Risoluzione problemi](#risoluzione-problemi)

## Cosa fa

- ⭐ Salva la tua **stazione preferita** e consulta i prezzi in un tap
- 📋 Legge **prezzi aggiornati** per tutti i carburanti (self / servito)
- 🏪 Mostra dettagli completi: brand, indirizzo, orari, servizi, link mappa
- 🔍 **Cerca stazioni** per nome, comune, indirizzo, brand
- 📍 Trova le **stazioni vicine** dalla posizione condivisa su Telegram
- 🏆 Trova il **miglior prezzo** in zona filtrando per carburante e modalità
- 🔔 Invia una **notifica giornaliera** automatica all'orario scelto

Dati da:
- **API Osservaprezzi** (prezzi e dettagli live)
- **CSV MIMIT** (anagrafica stazioni con coordinate, cache locale)

## Comandi disponibili

- `/start` mostra introduzione e setup rapido
- `/help` mostra aiuto
- `/station <id>` o `/stazione <id>` imposta la stazione preferita
- `/unset_station` rimuove la stazione preferita
- `/prezzi [id]` mostra prezzi e dettagli (se ometti `id` usa la stazione preferita)
- `/cerca <testo>` cerca stazioni per nome/comune/indirizzo/brand
- `/vicino [raggio_km]` trova stazioni vicine usando la posizione salvata
- `/vicino <lat> <lon> [raggio_km]` ricerca manuale per coordinate
- `/best <carburante> <self|servito> [raggio_km]` miglior prezzo vicino in base ai filtri
- `/notifica HH:MM` attiva notifica giornaliera (ora Italia)
- `/no_notifica` disattiva notifiche
- `/mia` mostra la configurazione utente corrente

Esempi rapidi:

```text
/station 12345
/prezzi
/cerca eni milano
/vicino 7
/best gasolio self 5
/notifica 08:30
```

## Uso con tastiera (senza slash commands)

Il bot è utilizzabile completamente da tastiera Telegram (pulsanti reply keyboard):

| Pulsante | Funzione |
|---|---|
| `📋 Prezzi stazione` | Prezzi della stazione preferita |
| `⭐ Imposta stazione` | Imposta stazione preferita per ID |
| `🔍 Cerca stazioni` | Ricerca per testo |
| `📍 Stazioni vicine` | Stazioni nel raggio 5 km dalla posizione salvata |
| `🏆 Miglior prezzo` | Selettore carburante + modalità via pulsanti inline |
| `📍 Condividi posizione` | Salva la posizione e mostra le stazioni vicine |
| `🔔 Imposta notifica` | Orario notifica giornaliera |
| `🔕 Disattiva notifiche` | Disattiva notifiche |
| `⚙️ Le mie impostazioni` | Riepilogo configurazione utente |
| `🗑 Rimuovi stazione` | Rimuove la stazione preferita (chiede conferma) |
| `❓ Aiuto` | Mostra il messaggio di benvenuto |
| `✖️ Annulla` | Annulla l'operazione corrente |

I comandi `/...` restano disponibili come alternativa.

### Tastiere inline

Oltre alla reply keyboard, il bot usa tastiere inline contestuali:

- **Risultati ricerca / Stazioni vicine**: pulsanti `⭐ Imposta` e `📋 Prezzi` per ogni stazione
- **Prezzi stazione**: pulsante `⭐ Imposta preferita`
- **Miglior prezzo**: selettore carburante → selettore self/servito → risultato con `⭐ Imposta`
- **Rimuovi stazione**: richiede conferma con `✅ Conferma` / `❌ Annulla`

## Prerequisiti

- Token Telegram Bot (da BotFather)
- Se esecuzione locale: Python 3.11+
- Se container: Docker (o Portainer su host Docker)

## Installazione locale (Python)

1. Crea ambiente virtuale:

```bash
cd bot_telegram
python3 -m venv .venv
source .venv/bin/activate
```

2. Installa dipendenze:

```bash
pip install -r requirements.txt
```

3. Configura variabili ambiente:

```bash
export TELEGRAM_BOT_TOKEN="INSERISCI_TOKEN_REALE"
export BOT_DATA_DIR=".bot_data"   # opzionale
export LOG_LEVEL="INFO"            # opzionale
```

4. Avvia:

```bash
python run_telegram_bot.py
```

## Installazione con Docker

Nel repository sono già presenti:

- `Dockerfile`
- `docker-compose.yml`

### Avvio con Docker Compose

1. Entra nella cartella dedicata:

```bash
cd bot_telegram
```

2. Crea il file `.env`:

```bash
cp .env.example .env
```

3. Modifica `.env` e imposta il token reale:

```env
TELEGRAM_BOT_TOKEN=INSERISCI_TOKEN_REALE
LOG_LEVEL=INFO
BOT_DATA_DIR=/data
```

4. Avvia:

```bash
docker compose up -d --build
```

5. Controlla i log:

```bash
docker compose logs -f
```

6. Stop:

```bash
docker compose down
```

## Pipeline CI/CD immagine Docker

È stata aggiunta la workflow GitHub Actions:

- `.github/workflows/docker-bot-image.yml`

Cosa fa:

- builda l'immagine dal contesto `bot_telegram/`
- pubblica su GHCR: `ghcr.io/<tuo-username>/osservaprezzi-telegram-bot`
- genera tag `latest` (branch default), `v*` (tag git), e `sha-*`
- build multi-arch: `linux/amd64` e `linux/arm64`

Trigger:

- `push` su `main` (solo quando cambia `bot_telegram/**`)
- `push` di tag `v*`
- `pull_request` (build senza push)
- `workflow_dispatch`

## Deploy con Portainer (Stack)

1. Apri Portainer.
2. Vai su `Stacks` -> `Add stack`.
3. Dai un nome allo stack (es. `osservaprezzi-bot`).
4. In `Web editor` incolla il contenuto di `docker-compose.yml`.
5. Crea variabili stack (o `.env`) e imposta almeno `TELEGRAM_BOT_TOKEN`.
6. Clicca `Deploy the stack`.

Esempio stack pronto:

```yaml
services:
  osservaprezzi-telegram-bot:
    build: .
    container_name: osservaprezzi-telegram-bot
    restart: unless-stopped
    environment:
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      BOT_DATA_DIR: ${BOT_DATA_DIR:-/data}
    volumes:
      - ./data:/data
```

Nota: se usi Portainer su host remoto, assicurati che il path `./data` sia persistente nel nodo dove gira lo stack.

### Deploy da immagine già buildata (senza build in Portainer)

Usa `docker-compose.image.yml`. Imposta nel `.env` (o nelle variabili stack di Portainer):

```env
GITHUB_USERNAME=tuo-username-github
TELEGRAM_BOT_TOKEN=...
```

Il compose userà automaticamente `ghcr.io/${GITHUB_USERNAME}/osservaprezzi-telegram-bot:latest`.

## Persistenza dati

Il bot salva su disco:

- cache stazioni (da CSV ufficiale)
- configurazioni utenti (stazione preferita, posizione, orario notifiche)

Con Docker la persistenza è gestita dal volume:

- host `./data` -> container `/data`

Non perdere i dati: evita di rimuovere la cartella/volume persistente.

## Come trovare l'ID stazione

1. Vai su `https://carburanti.mise.gov.it/ospzSearch/zona`
2. Cerca l'impianto desiderato
3. Apri il dettaglio della stazione
4. Copia l'ID dall'URL, ad esempio:
   `https://carburanti.mise.gov.it/ospzSearch/dettaglio/1111` -> ID `1111`

## Risoluzione problemi

- Errore `TELEGRAM_BOT_TOKEN non impostata`:
  - controlla variabile ambiente/token nello stack
- Il bot non risponde ai comandi:
  - verifica che il container sia `running`
  - controlla i log (`docker compose logs -f` o log stack Portainer)
- Nessun risultato in `/vicino` o `/best`:
  - invia posizione Telegram oppure usa coordinate manuali
- Notifiche non arrivate:
  - verifica `/mia` e che sia impostata una stazione + `/notifica HH:MM`
  - orario notifiche è in timezone Italia (`Europe/Rome`)
