"""Quick test to verify openai import works."""
try:
    from openai import OpenAI
    print("IMPORT OK")
    c = OpenAI(api_key="test-key", base_url="https://test.example.com/v1")
    print(f"CLIENT OK: {type(c).__name__}")
except ImportError as e:
    print(f"ImportError: {e}")
except Exception as e:
    print(f"Other: {type(e).__name__}: {e}")
