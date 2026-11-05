"""Importación de extractos. Carga todos los módulos de esta carpeta para registrar sus BankParser."""
import importlib
import pkgutil

from .base import BankParser, FormatError, ParsedTx, ParseResult, RawFile, Sheet, detect_parser, parsers, register

for _module in pkgutil.iter_modules(__path__):
    if _module.name not in {"base", "readers", "tabular", "service"}:
        importlib.import_module(f"{__name__}.{_module.name}")

__all__ = ["BankParser", "FormatError", "ParsedTx", "ParseResult", "RawFile", "Sheet", "detect_parser", "parsers", "register"]
