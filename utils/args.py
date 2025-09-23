import argparse
from datetime import timezone, timedelta

def validate_timezone(value) -> timedelta:
    """
    Validates the timezone input for argparse. Accepts values like +0, +2, -5.5, etc.
    """
    try:
        offset = float(value)
        if not -12.0 <= offset <= 14.0:
            raise ValueError("Timezone offset must be between -12 and +14 hours.")

        hours = int(offset)
        minutes = int((abs(offset) * 60) % 60)
        delta = timedelta(hours=hours, minutes=minutes)

        return timezone(delta if offset >= 0 else -delta)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid timezone offset '{value}': {e}")


def validate_log_level(value: str) -> str:
    levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if value.upper() in levels:
        return value.upper()
    else:
        raise argparse.ArgumentTypeError(f"Invalid log level: {value}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Global arguments for the application",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--log-lvl",
        default="INFO",
        type=validate_log_level,
        help="Set the logging level [DEBUG, INFO, WARNING, ERROR]",
    )
    parser.add_argument(
        "--log-path",
        type=str,
        help="Path to the log file. If not provided, logs will not be stored",
        required=False,
    )
    parser.add_argument("--rpc", type=str, help="RPC server http/s", required=True)

    parser.add_argument(
        "--disable-emojis",
        action="store_true",
        help="Disable emojis in dashboard output (use in case emojis break the table)",
    )

    parser.add_argument(
        "--hashes",
        action="store_true",
        help="Instead of emojis/symbols output validator's votes (hashes)",
    )

    parser.add_argument(
        "--refresh-per-second",
        type=float,
        help="Refresh rate of the table per second",
        required=False,
        default=3,
    )
    parser.add_argument(
        "--refresh-validators",
        type=float,
        help="Refresh validators every N second",
        required=False,
        default=30,
    )
    parser.add_argument(
        "--refresh-consensus-state",
        type=float,
        help="Refresh consensus state every N second",
        required=False,
        default=1,
    )
    parser.add_argument(
        "--refresh-upgrade-plan",
        type=float,
        help="Refresh upgrade plan every N second",
        required=False,
        default=60,
    )
    parser.add_argument(
        "--refresh-block-time",
        type=float,
        help="Refresh upgrade plan every N second",
        required=False,
        default=120,
    )
    parser.add_argument(
        "--refresh-node",
        type=float,
        help="Refresh latest height & node sync status every N second",
        required=False,
        default=5,
    )

    parser.add_argument(
        "--tz",
        type=validate_timezone,
        help="Timezone offset (e.g. +2, -5.5, +0). Must be between -12 and +14.",
        required=False,
        default=timezone.utc,
    )

    parser.add_argument(
        "--block-time-number",
        type=int,
        help="Number of the latest block to evaluate average block time",
        required=False,
        default=30,
    )

    parser.add_argument(
        "--columns",
        type=int,
        help="Number of columns in the main table",
        required=False,
        default=3,
    )

    parser.add_argument(
        "--genesis",
        type=str,
        help="URL to genensis.json file to parse validators",
        required=False,
    )

    args = parser.parse_args()

    return args


args = parse_args()
