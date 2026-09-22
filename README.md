# GoogleHora

Backend for a multi-user astrology app: Google Sign-In, MongoDB-backed
profiles and chat history, an ayanamsa/birth-time rectification engine
(Vedic dasha timelines), a quota-gated LLM chat layer, and room to add
Chinese astrology and palmistry as additional engines later.

See the design doc for the full architecture, data model, and roadmap.

## Structure

```
main.py                    FastAPI entrypoint -- assembles the routers below
app/
  config.py                 Settings, read from environment / .env
  db.py                     MongoDB (Motor) connection + collection accessors
  security.py                Google ID token verification + app JWT sessions
  models.py                   Pydantic request/response schemas
  quota.py                     Free / daily / ad-unlock question quota
  llm_gateway.py                Provider-agnostic Gemini/GPT gateway
  engines/
    vedic.py                     Dasha timeline, ayanamsa set, rectification search
    significators.py              Loads the event->planet rule matrix (Mongo, falls
                                   back to astro_rules.json)
  routers/
    auth.py       POST /auth/google
    me.py         GET  /me                                  (login continuity)
    profiles.py   POST /profiles, GET /profiles/{id}, POST /profiles/{id}/events
    rectify.py    POST /profiles/{id}/rectify[/answer], GET .../rectify/status
    chat.py       POST /conversations, GET /conversations[/{id}], POST .../messages
    ads.py        POST /ads/reward-callback                 (server-verified only)
scripts/
  seed_rules.py    One-time: loads astro_rules.json into the significator_rules
                   Mongo collection, tagged system="vedic"
astro_rules.json    Kept as the seed source for scripts/seed_rules.py
DecoupledAstroEngine.java    Retired duplicate implementation -- see the header
                              comment in the file. Not part of the running service.
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env   # fill in MongoDB URI, Google client ID, JWT secret, LLM keys
python -m scripts.seed_rules   # requires MongoDB running and reachable
uvicorn main:app --reload
```

Visit `/docs` for interactive API docs once it's running.

### A note on a fixed bug

The original prototype referenced pyswisseph ayanamsa constants as
`swe.SE_SIDM_LAHIRI` etc. The installed PyPI package actually names them
`swe.SIDM_LAHIRI` (no `SE_` prefix) -- the original code would raise
`AttributeError` the moment the engine was constructed. `app/engines/vedic.py`
uses the correct names and has been run end-to-end against a MongoDB-free
smoke test to confirm the dasha/rectification math executes.

### What's stubbed, not yet real

- **LLM providers** (`app/llm_gateway.py`): call the real Gemini/OpenAI APIs
  when a key is set in `.env`; return a labeled placeholder otherwise so the
  rest of the app is testable without live keys.
- **Ad reward verification** (`app/routers/ads.py`): the signature check is a
  placeholder HMAC scheme. Swap it for your chosen ad network's actual
  server-side verification (e.g. AdMob rewarded ads SSV) before relying on it.
- **Chinese astrology / palmistry engines**: not built yet; `app/engines/`
  is structured so they can be added alongside `vedic.py` per the design doc.
