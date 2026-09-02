"""Parser port."""

from typing import Protocol

from docparser.domain.parser_contract import (
    ParserDescriptor,
    ParseRequest,
    ParseResult,
    ParserHealth,
)


class DocumentParser(Protocol):
    """Parser port; complete execution failures raise ParserExecutionError.

    PARTIAL results carry page-local ParserError values. An adapter must not return
    FAILED without a usable neutral envelope.
    """

    def descriptor(self) -> ParserDescriptor: ...

    def health(self) -> ParserHealth: ...

    def parse(self, request: ParseRequest) -> ParseResult: ...
