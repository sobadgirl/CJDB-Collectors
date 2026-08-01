# Provider layout rules

- Each provider must occupy exactly one direct child under this directory.
- A simple provider may be a single `*_provider.py` file.
- A provider that needs multiple implementation files must be a package directory named after its namespace.
- Do not place shared sibling files such as `*_client.py`, `*_engine.py`, or `*_helper.py` next to providers.
- Provider-specific request, parsing, setup, and runtime code belongs inside that provider's file or package.
- Register provider classes with `register_data_provider`; registration keys are `DataProviderType` enum values and provider identity is `namespace`.
- Provider instances receive configuration only through `parameter_values`; do not add `settings`, config services, or application paths to providers.
- `setup()` takes no configuration argument and reads the values injected by `DataProviderService.get_provider()`.
