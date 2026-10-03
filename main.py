"""CLI entrypoint for quick testing without Streamlit.

Usage: python main.py "<indication>" "<mechanism>"
"""
import sys

from pipeline import run_pipeline


def main() -> None:
    if len(sys.argv) != 3:
        print('Usage: python main.py "<indication>" "<mechanism>"')
        sys.exit(1)

    indication, mechanism = sys.argv[1], sys.argv[2]
    report = run_pipeline(indication, mechanism)
    print(report)


if __name__ == "__main__":
    main()
