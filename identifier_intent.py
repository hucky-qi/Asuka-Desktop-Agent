"""Identifier roles and boolean qualification, independent of display snippets."""
import re
from dataclasses import dataclass

TOKEN = re.compile(r'(?<![a-z0-9])(?:[a-z][a-z0-9]*[-_])?[a-z]*\d+[a-z0-9]*(?![a-z0-9])', re.I)


def identifier_matches(text, value):
    # A numeric shorthand may match a course prefix; a full identifier cannot
    # lose its prefix or match an identifier with a different prefix.
    prefix = r'(?:[a-z]+[-_]?)?' if str(value).isdigit() else ''
    return bool(re.search(r'(?<![a-z0-9])'+prefix+re.escape(str(value))+r'(?![a-z0-9])', str(text), re.I))


def satisfied(matched, groups):
    return not groups or any(set(group) <= set(matched) for group in groups)


@dataclass(frozen=True)
class IdentifierPlan:
    identifiers: tuple[str, ...] = ()
    groups: tuple[tuple[str, ...], ...] = ()
    ambiguous: bool = False

    def resolved(self):
        if not self.ambiguous:
            return self.groups
        # An ambiguous original expression must be clarified, not decided by
        # a model guess.
        raise ValueError('多个标识之间的关系不明确：请说明“同时涉及”还是“任意一个/或”；未放宽条件')


def parse(question):
    text = str(question).casefold()
    # Mask attributes in place to retain connector positions, not all digits.
    patterns = [r'[“\"\']([^“”\"\']+)[”\"\']\s*(?:文件夹|目录)',
                r'(?:/Users/|~/|(?:desktop|downloads|documents|workspace)/)[^\s“”\"\']+', r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', r'\d{1,2}:\d{2}(?::\d{2})?',
                r'\d+(?:\.\d+)?\s*(?:[kmgt]i?b|bytes?|字节|天|周|年|月|小时|分钟|秒|days?|weeks?|years?)(?![a-z0-9])',
                r'\d+(?:\.\d+)?\s*(?:字节|天|周|年|月|小时|分钟|秒)',
                r'(?<![a-z0-9])v\d+(?:\.\d+)*(?![a-z0-9])',
                r'(?:版本|version|ver)\s*\d+(?:\.\d+)*', r'第\s*\d+\s*(?:个|项|页)',
                r'\d+\.\d+(?:\.\d+)*']
    for pattern in patterns:
        def mask(match):
            # Explicit identifier roles outrank a v-prefix convention.
            if re.fullmatch(r'v\d+',match[0]) and re.search(r'(?:编号|标识|课程|项目|\b(?:id|project|course))\s*[:：]?\s*$',text[max(0,match.start()-24):match.start()]):return match[0]
            return ' '*len(match[0])
        text = re.sub(pattern, mask, text)
    hits = list(TOKEN.finditer(text))
    # Keep spelling separators exactly; underscore and dash are not aliases.
    values = tuple(dict.fromkeys(m[0] for m in hits))
    if len(values)>16:raise ValueError('标识条件过多，请缩小本次条件')
    if not values:return IdentifierPlan()
    if len(values)==1:return IdentifierPlan(values,(values,))
    default_or = bool(re.search(r'任一|任意一个|任何一个|either|any of', text))
    default_and = bool(re.search(r'同时|均涉及|都涉及|both|all of', text))
    groups=[[]];seen=set();ambiguous=False
    for i,hit in enumerate(hits):
        value=hit[0]
        if value in seen:continue
        seen.add(value)
        if i:
            gap=text[hits[i-1].end():hit.start()]
            is_or=bool(re.search(r'或者|或|\bor\b',gap))
            is_and=bool(re.search(r'并且|以及|同时|和|与|\band\b',gap))
            if is_or and is_and:ambiguous=True
            if is_or or (not is_and and default_or):groups.append([])
            elif not is_and and not default_and:ambiguous=True
        groups[-1].append(value)
    # Mixed bracketed expressions need explicit clarification rather than
    # silently flattening parentheses. Unbracketed AND has usual precedence.
    if re.search(r'[()（）]',text) and len(groups)>1:ambiguous=True
    return IdentifierPlan(values,tuple(tuple(g) for g in groups if g),ambiguous)
