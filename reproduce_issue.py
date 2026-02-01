import asyncio
import sys

print(f"Python version: {sys.version}")

try:
    import modal
    print("Successfully imported modal")
except Exception as e:
    print(f"Failed to import modal: {e}")

try:
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print("Successfully set WindowsSelectorEventLoopPolicy")
except Exception as e:
    print(f"Failed to set WindowsSelectorEventLoopPolicy: {e}")
