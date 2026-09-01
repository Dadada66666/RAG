"""Parser port."""

from typing import Protocol

from docparser.domain.parser_contract import (
    ParserDescriptor,
    ParseRequest,
    ParseResult,
    ParserHealth,
)


class DocumentParser(Protocol):
    def descriptor(self) -> ParserDescriptor: ...

    def health(self) -> ParserHealth: ...

    def parse(self, request: ParseRequest) -> ParseResult: ...

