# -*- coding: utf-8 -*-

"""
Serialization of PENMAN graphs.
"""

from typing import Optional, Union, Type, Iterable, Iterator, Tuple, List
from collections import defaultdict
import re
import logging

from penman.types import (Identifier, Target)
from penman.tree import (Tree, is_atomic)
from penman.graph import Graph
from penman.model import Model
from penman.surface import (
    AlignmentMarker,
    Alignment,
    RoleAlignment
)
from penman.lexer import (
    PENMAN_RE,
    TRIPLE_RE,
    lex,
    TokenIterator,
)
from penman import layout


class PENMANCodec(object):
    """
    An encoder/decoder for PENMAN-serialized graphs.
    """
    # The valid tokens for node identifers.
    IDENTIFIERS = 'SYMBOL',
    #: The valid non-node targets of edges.
    ATOMS = set(['SYMBOL', 'STRING', 'INTEGER', 'FLOAT'])

    def __init__(self, model: Model = None):
        if model is None:
            model = Model()
        self.model = model

    def decode(self, s: str, triples: bool = False) -> Graph:
        """
        Deserialize PENMAN-notation string *s* into its Graph object.

        Args:
            s: a string containing a single PENMAN-serialized graph
            triples: if `True`, parse *s* as a triple conjunction
        Returns:
            The :class:`Graph` object described by *s*.
        Example:
            >>> codec = PENMANCodec()
            >>> codec.decode('(b / bark :ARG1 (d / dog))')
            <Graph object (top=b) at ...>
            >>> codec.decode(
            ...     'instance(b, bark) ^ instance(d, dog) ^ ARG1(b, d)',
            ...     triples=True
            ... )
            <Graph object (top=b) at ...>
        """
        if triples:
            _triples = self.parse_triples(s)
            g = Graph(_triples)
        else:
            tree = self.parse(s)
            g = layout.interpret(tree, self.model)
        return g

    def parse(self, s: str) -> Tree:
        """
        Parse PENMAN-notation string *s* into its tree structure.

        Args:
            s: a string containing a single PENMAN-serialized graph
        Returns:
            The tree structure described by *s*.
        Example:
            >>> codec = PENMANCodec()
            >>> codec.parse('(b / bark :ARG1 (d / dog))')
            ('b', [('/', 'bark', []), ('ARG1', ('d', [('/', 'dog', [])]), [])])
        """
        tokens = lex(s, pattern=PENMAN_RE)
        metadata = self._parse_comments(tokens)
        node = self._parse_node(tokens)
        return Tree(node, metadata=metadata)

    def _parse_comments(self, tokens: TokenIterator):
        """
        Parse PENMAN comments from *tokens* and return any metadata.
        """
        metadata = {}
        while tokens.peek().type == 'COMMENT':
            comment = tokens.next().text
            while comment:
                comment, found, meta = comment.rpartition('::')
                if found:
                    key, _, value = meta.partition(' ')
                    metadata[key] = value
        return metadata

    def _parse_node(self, tokens: TokenIterator):
        """
        Parse a PENMAN node from *tokens*.

        Nodes have the following pattern::

            Node := '(' ID ('/' Label)? Edge* ')'
        """
        tokens.expect('LPAREN')

        id = None
        edges = []

        if tokens.peek().type != 'RPAREN':
            id = tokens.expect(*self.IDENTIFIERS).value
            if tokens.peek().type == 'SLASH':
                edges.append(self._parse_node_label(tokens))
            while tokens.peek().type != 'RPAREN':
                edges.append(self._parse_edge(tokens))

        tokens.expect('RPAREN')

        return (id, edges)

    def _parse_node_label(self, tokens: TokenIterator):
        tokens.expect('SLASH')
        label = None
        epis = []
        # for robustness, don't assume next token is the label
        if tokens.peek().type in self.ATOMS:
            label = tokens.next().value
            if tokens.peek().type == 'ALIGNMENT':
                epis.append(
                    self._parse_alignment(tokens, Alignment))
        return ('/', label, epis)

    def _parse_edge(self, tokens: TokenIterator):
        """
        Parse a PENMAN edge from *tokens*.

        Edges have the following pattern::

            Edge := Role (Constant | Node)
        """
        epidata = []
        role = tokens.expect('ROLE').text
        if tokens.peek().type == 'ALIGNMENT':
            epidata.append(
                self._parse_alignment(tokens, RoleAlignment))
        target = None

        _next = tokens.peek()
        next_type = _next.type
        if next_type in self.ATOMS:
            target = tokens.next().value
            if tokens.peek().type == 'ALIGNMENT':
                epidata.append(
                    self._parse_alignment(tokens, Alignment))
        elif next_type == 'LPAREN':
            target = self._parse_node(tokens)
        # for robustness in parsing, allow edges with no target:
        #    (x :ROLE :ROLE2...  <- followed by another role
        #    (x :ROLE )          <- end of node
        elif next_type not in ('ROLE', 'RPAREN'):
            raise tokens.error('Expected: ATOM, LPAREN', token=_next)

        return (role, target, epidata)

    def _parse_alignment(self,
                         tokens: TokenIterator,
                         cls: Type[AlignmentMarker]):
        """
        Parse a PENMAN surface alignment from *tokens*.
        """
        token = tokens.expect('ALIGNMENT')
        m = re.match((r'~(?P<prefix>[a-zA-Z]\.?)?'
                      r'(?P<indices>\d+(?:,\d+)*)'),
                     token.text)
        if m is not None:
            prefix = m.group('prefix')
            indices = tuple(map(int, m.group('indices').split(',')))
        else:
            prefix, indices = None, ()
        return cls(indices, prefix=prefix)

    def parse_triples(self, s: str):
        tokens = lex(s, pattern=TRIPLE_RE)
        target: Target

        triples = []
        while True:
            role = tokens.expect('SYMBOL').text
            tokens.expect('LPAREN')
            source = tokens.expect(*self.IDENTIFIERS).text
            tokens.expect('COMMA')
            _next = tokens.peek().type
            if _next in self.ATOMS:
                target = tokens.next().value
            elif _next == 'RPAREN':  # special case for robustness
                target = None
            tokens.expect('RPAREN')

            triples.append((source, role, target))

            # continue only if triple is followed by ^
            if tokens.peek().type == 'CARET':
                tokens.next()
            else:
                break

        return triples

    def encode(self,
               g: Graph,
               top: Identifier = None,
               triples: bool = False,
               indent: Optional[int] = -1,
               compact: bool = False) -> str:
        """
        Serialize the graph *g* into PENMAN notation.

        Args:
            g: the Graph object
            top: if given, the node to use as the top in serialization
            triples: if True, serialize as a conjunction of logical triples
            indent: how to indent formatted strings
            compact: if `True`, put initial attributes on the first line
        Returns:
            the PENMAN-serialized string of the Graph *g*
        Example:

            >>> codec = PENMANCodec()
            >>> codec.encode(Graph([('h', 'instance', 'hi')]))
            (h / hi)
            >>> codec.encode(Graph([('h', 'instance', 'hi')]),
            ...                      triples=True)
            instance(h, hi)

        """
        if triples:
            return self.format_triples(g, indent=(indent is not None))
        else:
            tree = layout.configure(g, top=top, model=self.model)
            return self.format(tree, indent=indent, compact=compact)

    def format(self, tree, indent: Optional[int] = -1, compact: bool = False):
        """
        Format *tree* into a PENMAN string.
        """
        if not isinstance(tree, Tree):
            tree = Tree(tree)
        ids = [id for id, _ in tree.nodes()] if compact else []
        parts = ['# ::{} {}'.format(key, value)
                 for key, value in tree.metadata.items()]
        parts.append(self._format_node(tree.node, indent, 0, set(ids)))
        return '\n'.join(parts)

    def _format_node(self,
                     node,
                     indent: Optional[int],
                     column: int,
                     ids: set) -> str:
        """
        Format tree *node* into a PENMAN string.
        """
        id, edges = node
        if not id:
            return '()'  # empty node
        if not edges:
            return '({!s})'.format(id)  # id-only node

        # determine appropriate joiner based on value of indent
        if indent is None:
            joiner = ' '
        else:
            if indent == -1:
                column += len(str(id)) + 2  # +2 for ( and a space
            else:
                column += indent
            joiner = '\n' + ' ' * column

        # format the edges and join them
        # if ids is non-empty, all initial attributes are compactly
        # joined on the same line, otherwise they use joiner
        parts: List[str] = []
        compact = bool(ids)
        for edge in edges:
            target = edge[1]
            if compact and (not is_atomic(target) or target in ids):
                compact = False
                if parts:
                    parts = [' '.join(parts)]
            parts.append(self._format_edge(edge, indent, column, ids))
        # check if all edges can be compactly written
        if compact:
            parts = [' '.join(parts)]

        return '({!s} {})'.format(id, joiner.join(parts))

    def _format_edge(self, edge, indent, column, ids):
        """
        Format tree *edge* into a PENMAN string.
        """
        role, target, epidata = edge

        if role != '/' and not role.startswith(':'):
            role = ':' + role

        role_epi = ''.join(str(epi) for epi in epidata if epi.mode == 1)
        target_epi = ''.join(str(epi) for epi in epidata if epi.mode == 2)

        if indent == -1:
            column += len(role) + len(role_epi) + 1 # +1 for :

        if target is None:
            target = ''
        elif not is_atomic(target):
            target = self._format_node(target, indent, column, ids)


        return '{}{} {!s}{}'.format(
            role,
            role_epi,
            target,
            target_epi)

    def format_triples(self,
                       g: Graph,
                       indent: bool = True):
        delim = ' ^\n' if indent else ' ^ '
        return delim.join(
            map('{0[1]}({0[0]}, {0[2]})'.format, g.triples())
        )
