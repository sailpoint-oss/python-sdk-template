# SailPoint Python SDK Template

A template project for the [SailPoint Python SDK](https://developer.sailpoint.com/docs/tools/sdk/python). It is initialized using the [SailPoint CLI](https://developer.sailpoint.com/docs/tools/cli) and provides working examples of common Identity Security Cloud API calls using the Python SDK.

## Prerequisites

- **Python 3.7+** — check with `python --version` ([download](https://www.python.org/downloads/))
- **SailPoint CLI** installed and configured ([installation guide](https://developer.sailpoint.com/docs/tools/cli))
- A SailPoint Identity Security Cloud tenant
- A **Personal Access Token (PAT)** with a client ID and client secret ([creating a PAT](https://developer.sailpoint.com/docs/api/authentication#personal-access-tokens))

## Getting Started

### 1. Create the project

Use the SailPoint CLI to create a new project from this template:

```bash
sail sdk init python py-example
```

This creates the following structure:

```
py-example/
├── requirements.txt
└── sdk.py
```

### 2. Navigate into the project

```bash
cd py-example
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> **Tip:** Use a [virtual environment](https://docs.python.org/3/library/venv.html) to isolate dependencies: `python -m venv .venv` then `source .venv/bin/activate` (Linux/macOS) or `.venv\Scripts\activate` (Windows).

### 4. Configure the SDK

The SDK needs credentials to authenticate with your SailPoint tenant. Generate a `config.json` file using the CLI:

```bash
sail sdk init config
```

If you have multiple environments configured in the CLI, specify which one to use:

```bash
sail sdk init config --env devrel
```

This creates a `config.json` in the project directory:

```json
{
  "ClientId": "your-client-id",
  "ClientSecret": "your-client-secret",
  "BaseURL": "https://[tenant].api.identitynow.com"
}
```

You can also use environment variables (`SAIL_BASE_URL`, `SAIL_CLIENT_ID`, `SAIL_CLIENT_SECRET`) instead of a config file.

> **Tip:** Add `config.json` to your `.gitignore` so credentials are not committed to version control.

### 5. Run the project

```bash
python sdk.py
```

## What's Included

The `sdk.py` file contains several example operations demonstrating common SDK usage:

| Operation                           | API  | SDK Class                   |
| ----------------------------------- | ---- | --------------------------- |
| List transforms                     | V3   | `TransformsApi`             |
| List access profiles                | V3   | `AccessProfilesApi`         |
| Search identities (with pagination) | V3   | `SearchApi` + `Paginator`   |
| Paginate accounts                   | V3   | `AccountsApi` + `Paginator` |
| List governance groups (workgroups) | Beta | `GovernanceGroupsApi`       |

You can extend the script with any [V3](https://developer.sailpoint.com/docs/api/v3) or [Beta](https://developer.sailpoint.com/docs/api/beta) API endpoints.

## Customization

- **Change the API** — swap `TransformsApi` for another API such as `AccountsApi` or `SourcesApi`.
- **Change the method** — use different methods on the API client for your use case.
- See the [Getting Started guide](https://developer.sailpoint.com/docs/tools/sdk/python/getting-started) for more examples.

## Resources

- [Python SDK Documentation](https://developer.sailpoint.com/docs/tools/sdk/python)
- [Python SDK Getting Started Guide](https://developer.sailpoint.com/docs/tools/sdk/python/getting-started)
- [SailPoint CLI Documentation](https://developer.sailpoint.com/docs/tools/cli)
- [Identity Security Cloud API Reference](https://developer.sailpoint.com/docs/api/)
- [Python SDK GitHub Repository](https://github.com/sailpoint-oss/python-sdk)
- [SailPoint Developer Community](https://developer.sailpoint.com/discuss)
