import asyncio
import warnings
import sys

# Filter warnings to make sure we see them
warnings.simplefilter('always', DeprecationWarning)

print(f"Python Version: {sys.version}")

try:
    print("Importing modal...")
    import modal
    print("Modal imported successfully.")
except Exception as e:
    print(f"Failed to import modal: {e}")

try:
    print("Checking Event Loop Policy...")
    policy = asyncio.get_event_loop_policy()
    print(f"Current Policy: {policy}")
    
    # Check if WindowsSelectorEventLoopPolicy exists (it does on Py3.14 but raises warning on use/instantiation)
    if hasattr(asyncio, 'WindowsSelectorEventLoopPolicy'):
        print("Instantiating WindowsSelectorEventLoopPolicy to trigger warning...")
        asyncio.WindowsSelectorEventLoopPolicy()
        print("Instantiation complete.")
    else:
        print("WindowsSelectorEventLoopPolicy not found (Removed?)")
        
except Exception as e:
    print(f"Error during check: {e}")
