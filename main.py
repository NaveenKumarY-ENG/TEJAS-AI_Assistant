"""
CLI entry point. Run: python main.py

This is the text-first interface. Voice/web UI come later as thin layers
on top of Agent.chat() — the core logic here doesn't change.
"""
import logging
import sys

from agent import Agent
from config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.sandbox_dir + "/../assistant.log"),
        logging.StreamHandler(sys.stdout) if "--debug" in sys.argv else logging.NullHandler(),
    ],
)
logger = logging.getLogger("assistant.main")


def main():
    # if not config.anthropic_api_key:
    #     print("ERROR: ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key.")
    #     sys.exit(1)

    resume = "--resume" in sys.argv
    agent = Agent(resume=resume)

    mode = "resumed previous session" if resume and agent.history else "new session"
    print(f"{config.assistant_name} online ({mode}). Type 'exit' to quit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nShutting down.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            print("Shutting down.")
            break

        try:
            print(f"{config.assistant_name}: ", end="", flush=True)
            agent.chat_streaming(user_input, lambda piece: print(piece, end="", flush=True))
            print("\n")
        except Exception as e:
            logger.exception("Unhandled error during chat turn")
            print(f"\n[Error: {e}]\n")


if __name__ == "__main__":
    main()