#!/usr/bin/env python3
"""QRelia LCD production launcher."""
import asyncio
import qrelia_device as runtime

if __name__ == "__main__":
    try:
        asyncio.run(runtime.main())
    finally:
        runtime.shutdown_hardware()
