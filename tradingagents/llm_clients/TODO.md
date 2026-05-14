# LLM Clients - Consistency Improvements

## Issues to Fix

### 1. ~~`validate_model()` is never called~~ (Fixed)
- `BaseLLMClient.warn_if_unknown_model()` calls `validate_model()` and emits
  `RuntimeWarning` for unknown models without raising. Each provider's
  `get_llm()` (openai/anthropic/google/azure) calls it on the first line.
  Covered by `tests/test_model_validation.py`.

### 2. ~~Inconsistent parameter handling~~ (Fixed)
- GoogleClient now accepts unified `api_key` and maps it to `google_api_key`

### 3. ~~`base_url` accepted but ignored~~ (Fixed)
- All clients now pass `base_url` to their respective LLM constructors

### 4. ~~Update validators.py with models from CLI~~ (Fixed)
- Synced in v0.2.2
