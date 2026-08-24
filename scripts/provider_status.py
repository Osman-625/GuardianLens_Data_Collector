"""Shows the active AI_PROVIDER/model, which providers are configured, what models
each configured provider currently offers, and how many listings sit in each
pipeline stage. Read-only: makes no state-changing calls."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from guardianlens.config import settings, get_active_provider, provider_model, provider_configured, AI_PROVIDERS
from guardianlens.provider_info import MODEL_LISTERS
from guardianlens.states import TRANSITIONS
from guardianlens.db import connect

def section(title: str) -> None:
    print(f"\n=== {title} ===")

def main() -> None:
    active = get_active_provider()
    section("AI provider configuration")
    print(f"active provider = {active!r} (AI_PROVIDER in .env = {settings.ai_provider!r})")
    for name in AI_PROVIDERS:
        mark = "ACTIVE" if name == active else ("configured" if provider_configured(name) else "not configured")
        print(f"  {name:<12} [{mark:<13}] model={provider_model(name)}")

    section("Models available per configured provider")
    any_configured = False
    for name in AI_PROVIDERS:
        if not provider_configured(name):
            continue
        any_configured = True
        print(f"{name}:")
        try:
            models = MODEL_LISTERS[name]()
            found = provider_model(name) in models
            label = "free+vision models" if name == "openrouter" else "models visible to this key"
            print(f"  {len(models)} {label}. Configured model {provider_model(name)!r} found: {found}")
            for m in models:
                marker = " <- configured" if m == provider_model(name) else ""
                print(f"    {m}{marker}")
        except Exception as e:
            print(f"  ERROR calling {name} models API: {e}")
    if not any_configured:
        print("  No provider has an API key set in .env.")

    section(f"Pipeline stages (listings.status) — {settings.mode} database: {settings.db_path}")
    con = connect()
    counts = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM listings GROUP BY status")}
    con.close()
    for state in TRANSITIONS.keys():
        print(f"  {state:<18} {counts.get(state, 0)}")
    unknown = set(counts) - set(TRANSITIONS.keys())
    for state in sorted(unknown):
        print(f"  {state:<18} {counts[state]}  (not in states.TRANSITIONS)")

if __name__ == "__main__":
    main()
