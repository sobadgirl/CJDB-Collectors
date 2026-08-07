# Provider layout rules

- Each provider must occupy exactly one direct child under this directory.
- A simple provider may be a single `*_provider.py` file.
- A provider that needs multiple implementation files must be a package directory named after its namespace.
- Do not place shared sibling files such as `*_client.py`, `*_engine.py`, or `*_helper.py` next to providers.
- Provider-specific request, parsing, setup, and runtime code belongs inside that provider's file or package.
- Register provider classes with `register_data_provider`; registration keys are `DataProviderType` enum values and provider identity is `namespace`.
- Provider instances receive persisted runtime state only through the constructor `setup_payload`; do not add settings services or application paths to providers.
- Setup params are transient. `setup(params)` returns the runtime state to persist in `SetupResult.setup_payload`; later instances receive only that payload through their constructor.
