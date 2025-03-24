import logging
import sys

# Create a custom logger
logger = logging.getLogger("bemo_chatbot")
logger.setLevel(logging.DEBUG)  # or INFO depending on your needs

# Create handlers: here, a stream handler for the console.
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)

# Create a formatter and attach it to the handler.
formatter = logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
console_handler.setFormatter(formatter)

# Avoid duplicate handlers if already set.
if not logger.handlers:
    logger.addHandler(console_handler)
