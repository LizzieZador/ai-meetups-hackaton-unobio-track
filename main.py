"""CLI entrypoint for quick testing without Streamlit.

Usage: python main.py "<indication>" "<mechanism>" ["<extra context>"]
"""
import sys

from pipeline import run_pipeline_markdown


def main() -> None:
    if len(sys.argv) not in (3, 4):
        print('Usage: python main.py "<indication>" "<mechanism>" ["<extra context>"]')
        sys.exit(1)

    indication, mechanism = sys.argv[1], sys.argv[2]
    extra_context = sys.argv[3] if len(sys.argv) == 4 else ""
    report = run_pipeline_markdown(indication, mechanism, extra_context)
    print(report)


if __name__ == "__main__":
    main()
