"""`python -m ft_bot` runs the Telegram long-polling bot."""

from ft_bot.service import run_polling


def main() -> None:
    run_polling()


if __name__ == "__main__":
    main()
