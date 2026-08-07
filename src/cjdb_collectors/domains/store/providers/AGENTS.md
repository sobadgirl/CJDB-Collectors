# Provider layout rules

- Each store provider must occupy exactly one direct child under this directory.
- A simple provider may be a single `*_provider.py` file.
- A provider that needs multiple implementation files must be a package directory named after its provider type or namespace.
- Do not place shared sibling files such as `*_client.py`, `*_engine.py`, or `*_helper.py` next to providers.
- Provider-specific request, parsing, setup, and runtime code belongs inside that provider's file or package.
- Provider instances receive persisted runtime state through `setup_payload`. Setup params are transient, and successful `setup()` implementations must return the payload to persist in `SetupResult.setup_payload`.
