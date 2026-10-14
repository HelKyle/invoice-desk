"""Entry point for the desktop service and directly launched stdio worker."""
from engine.startup_debug import configure, trace

configure()
trace("service-import-begin", module_name=__name__)
from engine.service import main
trace("service-import-end", module_name=__name__)

if __name__ == "__main__":
    main()
