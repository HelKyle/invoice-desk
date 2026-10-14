"""PyInstaller entry point; retain a real file for multiprocessing spawn."""
import multiprocessing
import os
from engine.service import main

if __name__ == "__mp_main__" and os.environ.get("INVOICE_TRACE_WORKER_BOOTSTRAP") == "1":
    import faulthandler
    faulthandler.dump_traceback_later(15)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
