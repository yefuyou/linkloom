"""Read-only setup guidance shared by the launcher and product status."""

GEMINI_CONFIGURATION_HELP = (
    "Set GEMINI_API_KEY in the environment of the terminal, IDE or service that starts LinkLoom; "
    "see .env.example. PowerShell: $env:GEMINI_API_KEY = '<your-key>'; "
    "macOS/Linux: export GEMINI_API_KEY='<your-key>'. "
    "LinkLoom does not load .env automatically. Restart the service with "
    "--allow-external-provider to enable AI extraction."
)

OFFLINE_HELP = (
    "Without an API key, you can create workspaces, save sources locally, review existing "
    "candidates, and query approved decisions and their history. "
    "For a synthetic offline demo, start with --offline-task-demo using a separate database. "
    "AI extraction and external Agent investigations require provider configuration and explicit permissions."
)
