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
- 👑 **Pannello admin**: lista utenti, blocco/sblocco, broadcast, messaggi diretti

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

### Comandi admin (solo per gli ID in `ADMIN_TELEGRAM_ID`)

- `/admin` apre il pannello admin con statistiche e lista utenti
- `/broadcast <messaggio>` invia un messaggio a tutti gli utenti non bloccati
- `/msg <user_id> <messaggio>` invia un messaggio diretto a un utente
- `/block <user_id>` blocca un utente
- `/unblock <user_id>` sblocca un utente

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

Il bot è completamente utilizzabile tramite pulsanti (reply keyboard). La tastiera si adatta al ruolo dell'utente.

### Tastiera utente

| Pulsante | Funzione |
|---|---|
| `📋 Prezzi stazione` | Prezzi della stazione preferita |
| `⭐ Imposta stazione` | Imposta stazione preferita per ID |
| `🔍 Cerca stazioni` | Ricerca per nome, comune, brand |
| `📍 Stazioni vicine` | Stazioni nel raggio 5 km dalla posizione salvata |
| `🏆 Miglior prezzo` | Selettore carburante + modalità via pulsanti inline |
| `🔎 Servizi vicini` | Cerca autolavaggi, ricarica elettrica, bancomat e altri servizi |
| `📍 Condividi posizione` | Salva la posizione e mostra le stazioni vicine |
| `⚙️ Impostazioni` | Riepilogo configurazione + notifiche e rimozione stazione |
| `❓ Aiuto` | Mostra il messaggio di benvenuto |

### Pulsante admin (visibile solo agli admin)

| Pulsante | Funzione |
|---|---|
| `👑 Admin` | Apre il pannello admin con statistiche, lista utenti, blocco/sblocco |

### Tastiere inline contestuali

- **Risultati ricerca / Stazioni vicine**: `⭐ Imposta` e `📋 Prezzi` per ogni stazione
- **⚙️ Impostazioni**: mostra stazione/posizione/notifica correnti e offre `📋 Vedi prezzi`, `🗑 Rimuovi stazione`, `🔔/🔕 Notifiche`
- **Miglior prezzo**: selettore carburante → selettore self/servito → risultato con `⭐ Imposta`
- **Servizi vicini**: selezione tipo servizio → lista stazioni con quel servizio
- **Rimuovi stazione**: richiede conferma con `✅ Conferma` / `❌ Annulla`
- **Ogni prompt di testo** (inserisci ID, cerca, orario notifica): pulsante inline `❌ Annulla`

I comandi `/...` restano disponibili come alternativa ai pulsanti.

## Prerequisiti

- Token Telegram Bot (da BotFather)
- Se esecuzione locale: Python 3.11+
- Se container: Docker (o Portainer su host Docker)

## Installazione locale (Python)

1. Crea ambiente virtuale:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Installa dipendenze:

```bash
pip install -r requirements.txt
```

3. Configura variabili ambiente:

```bash
export TELEGRAM_BOT_TOKEN="il-tuo-token"
export ADMIN_TELEGRAM_ID="123456789"   # opzionale, trovalo con @userinfobot
export BOT_DATA_DIR=".bot_data"        # opzionale
export LOG_LEVEL="INFO"                # opzionale
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

1. Crea il file `.env`:

```bash
cp .env.example .env
```

2. Modifica `.env`:

```env
TELEGRAM_BOT_TOKEN=il-tuo-token
LOG_LEVEL=INFO
BOT_DATA_DIR=/data
ADMIN_TELEGRAM_ID=123456789
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

Il file `.github/workflows/docker-bot-image.yml` automatizza la build e il push su GHCR.

Cosa fa:

- builda l'immagine per `linux/amd64`
- pubblica su GHCR: `ghcr.io/<tuo-username>/carburanti-bot`
- genera i tag `latest` (branch default), `v*` (tag git), `sha-*`

Trigger:

- `push` su `main`
- `push` di tag `v*`
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
- Il pulsante `👑 Admin` non appare:
  - verifica che `ADMIN_TELEGRAM_ID` contenga il tuo ID Telegram (trovalo con `@userinfobot`)
  - riavvia il bot dopo aver impostato la variabile
  - supporta più admin separati da virgola: `111111,222222`
