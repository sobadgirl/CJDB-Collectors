# Provider layout rules

- Each store provider must occupy exactly one direct child under this directory.
- A simple provider may be a single `*_provider.py` file.
- A provider that needs multiple implementation files must be a package directory named after its provider type or namespace.
- Do not place shared sibling files such as `*_client.py`, `*_engine.py`, or `*_helper.py` next to providers.
- Provider-specific request, parsing, setup, and runtime code belongs inside that provider's file or package.
