"""PyInstaller entry point; retain a real file for multiprocessing spawn."""
import multiprocessing
from engine.service import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
