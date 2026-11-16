# tailor-cv
This project is aimed for anyone who is job-hunting and wishes to use an AI-first tool to tailor their CV, using their own skills and experience, to each individual position. This is a fine tuner based on your own CV (CV creator script is included) and will not make up any experience.

## Features
- **Dual-mode tailoring (`auto`, `ai`, `local`)**:
  - Uses free-tier AI APIs (**Gemini**, **Mistral**, or **OpenAI**) when configured, with strict validation so skills, companies, dates, and bullets are never fabricated.
  - Automatically falls back to a deterministic **local keyword & alias matcher** when no API key is set or offline.
- **Privacy-first profile separation**: Personal details live in `data/profile.json` (gitignored), while `data/profile.example.json` serves as the public template.
- **PDF generation**: Preserves your ReportLab two-column CV layout with support for multiple profile variants (e.g. `eu`, `latam`).
- **Application tracker**: Logs every tailored CV into SQLite (`output/applications.db`) with status updates and CSV export.

## Setup

```bash
pip install -r requirements.txt
cp data/profile.example.json data/profile.json
cp .env.example .env
```

Edit `data/profile.json` with your real details, and optionally add a free-tier `GEMINI_API_KEY`, `MISTRAL_API_KEY`, or `OPENAI_API_KEY` in `.env`.

## Usage

Generate a base CV without tailoring:
```bash
python -m src.main generate --variant eu
```

Tailor your CV for a specific job offer:
```bash
python -m src.main tailor \
  --company "Acme Corp" \
  --role "Senior QA Automation Engineer" \
  --variant eu \
  --jd-text "Looking for a QA Automation Engineer with Playwright, Python, SQL, and Linux experience."
```

Or pass a job description file:
```bash
python -m src.main tailor --company "Acme Corp" --role "QA Engineer" --jd-file job.txt
```

Track and manage applications:
```bash
python -m src.main list
python -m src.main status --id 1 --status applied
python -m src.main export --out output/applications.csv
```
