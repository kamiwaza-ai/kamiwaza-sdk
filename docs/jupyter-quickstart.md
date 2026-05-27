# Jupyter Quickstart

Run the SDK's example notebooks against a live Kamiwaza dev cluster, from a single command.

## Prerequisites

- Python 3.10+ (3.12 recommended)
- A Kamiwaza dev cluster reachable from your machine. The default is the local k0s-lima dev stack on `https://localhost/api` (see [`kamiwaza` repo install-dev.sh](https://github.com/kamiwaza-internal/kamiwaza/blob/develop/services/core/scripts/install-dev.sh)), but any reachable endpoint works.

## Install

```bash
pip install kamiwaza-sdk[notebook]
```

The `[notebook]` extra pulls in JupyterLab + ipykernel + ipywidgets + nbclient + nbformat — everything needed to launch a working JupyterLab environment.

## Launch

From the SDK repo root:

```bash
./scripts/start-jupyter-lab.sh
```

This:

1. Kills any previously-running `jupyter-lab` process (port `8890`) to avoid collisions on rerun
2. Reads the version-controlled config from `examples/.jupyter/jupyter_lab_config.py`
3. Exports `KAMIWAZA_BASE_URL` to the JupyterLab subprocess so notebook code can pick it up
4. Launches JupyterLab in the background, writing to `jupyter_lab.log` in the repo root
5. Prints the `http://localhost:8890/lab` URL

The default endpoint is `https://localhost/api` (the kamiwaza k0s-lima dev default). Override:

```bash
KAMIWAZA_BASE_URL=https://my-cluster.example.com/api ./scripts/start-jupyter-lab.sh
```

## Local k0s-lima dev workflow

The most common local-dev setup pairs this SDK with a local kamiwaza dev cluster on macOS via k0s-lima:

1. **Bring up the kamiwaza dev cluster** (in the kamiwaza repo):
   ```bash
   cd ~/devel/kz/kamiwaza
   ./services/core/scripts/install-dev.sh
   ```
   This stands up a local k0s cluster inside a Lima VM, installs the platform charts, and seeds an admin persona. See the [kamiwaza install-dev docs](https://github.com/kamiwaza-internal/kamiwaza/blob/develop/services/core/scripts/install-dev.sh) for prereqs (lima, kind, helmfile).

2. **Mint a Personal Access Token (PAT)** for SDK use:
   ```bash
   export KAMIWAZA_BASE_URL=https://localhost/api
   python -c "
   from kamiwaza_sdk import KamiwazaClient
   from kamiwaza_sdk.authentication import UserPasswordAuthenticator
   from kamiwaza_sdk.schemas.auth import PATCreate

   client = KamiwazaClient(KAMIWAZA_BASE_URL := 'https://localhost/api')
   client.authenticator = UserPasswordAuthenticator('admin', 'kamiwaza', client.auth)
   print('KAMIWAZA_API_KEY=' + client.auth.create_pat(PATCreate(name='jupyter-local')).token)
   "
   ```
   Add the exported line to your shell rc.

3. **Launch JupyterLab** from this SDK repo:
   ```bash
   cd ~/devel/kz/kamiwaza-sdk
   ./scripts/start-jupyter-lab.sh
   ```

4. **Open** `http://localhost:8890/lab` and run any notebook from `examples/`. The SDK auto-loads `KAMIWAZA_API_KEY` from the env.

## Pointing at a different cluster

The same launcher works against any reachable Kamiwaza endpoint:

```bash
# Spark dev cluster
KAMIWAZA_BASE_URL=https://spark-2.internal/api ./scripts/start-jupyter-lab.sh

# Customer staging
KAMIWAZA_BASE_URL=https://staging.customer.example.com/api ./scripts/start-jupyter-lab.sh
```

For TLS-verification trade-offs, see [kamiwaza_sdk TLS docs](../README.md#tls-verification) — for dev clusters with self-signed certs, install the CA into your trust store rather than disabling verification.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `jupyter: command not found` | Notebook extra not installed | `pip install kamiwaza-sdk[notebook]` |
| Browser opens but notebooks fail at `KamiwazaClient(...)` with `Connection refused` | `KAMIWAZA_BASE_URL` points at a cluster that isn't running | Verify with `curl ${KAMIWAZA_BASE_URL}/health` |
| `401 Unauthorized` on every SDK call | No `KAMIWAZA_API_KEY` exported, or token is for a different cluster | Re-mint the PAT against the right cluster (see step 2) |
| Port 8890 already in use | Another JupyterLab process didn't get cleaned up | The launcher will kill it on next run; or `pkill -f jupyter-lab` |
| TLS verification error against dev cluster | Self-signed cert | Add the dev cluster's CA to your OS trust store (don't disable verification — `verify=False` opens you to MITM even on a dev cluster, and the habit carries into production code) |

## What `examples/.jupyter/jupyter_lab_config.py` configures

A minimal config — no auth, base URL at `/lab`, lab port `8890`. Suitable for **local dev only**. Don't expose this configuration to the network without adding token/password auth and TLS termination at a reverse proxy.

## Related

- `examples/` — the notebooks that ship with this SDK (10 numbered tutorials + `interactive_rag.py` + supporting docs)
- `scripts/start-jupyter-lab.sh` — the launcher this doc covers
- `examples/.jupyter/jupyter_lab_config.py` — the launcher's config source
