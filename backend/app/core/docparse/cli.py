import logging
import sys

import click

from .convert import convert_pdf_to_markdown


@click.group()
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def main(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)s %(name)s: %(message)s')


@main.command()
@click.argument('pdf_path', type=click.Path(exists=True, dir_okay=False))
@click.option('--output', '-o', required=True, help='Output Markdown file path')
@click.option('--debug', is_flag=True, help='Save debug files to _debug/')
def convert(pdf_path: str, output: str, debug: bool) -> None:
    """Convert a PDF file to structured Markdown."""
    try:
        convert_pdf_to_markdown(pdf_path=pdf_path, output_path=output, debug=debug)
    except Exception as exc:
        click.echo(f'Error: {exc}', err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
