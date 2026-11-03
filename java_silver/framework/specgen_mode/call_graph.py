import re

def roots(program, emitted):
    # Use resolved calls from our emitted closed subset, not name-only guesses
    # from source (Math.abs must not become a call to a user method named abs).
    methods = dict(program.methods)
    edges = {key: set() for key in methods}
    names = {m.silver: key for key, m in methods.items()}
    starts = list(re.finditer(r'^method ([A-Za-z_][A-Za-z_0-9]*)\(', emitted, re.M))
    for i, start in enumerate(starts):
        key = names.get(start.group(1))
        if key is None:
            continue
        end = starts[i + 1].start() if i + 1 < len(starts) else len(emitted)
        body = emitted[start.end():end]
        for called in re.findall(r'\b([A-Za-z_][A-Za-z_0-9]*)\s*\(', body):
            if called in names:
                edges[key].add(names[called])
    def reachable(k):
        todo, seen = [k], set()
        while todo:
            v = todo.pop()
            if v in seen:
                continue
            seen.add(v)
            todo.extend(edges[v])
        return seen
    reach = {k: reachable(k) for k in methods}
    # All members of a source SCC are roots. No arbitrary input domain inferred.
    result = [k for k in methods if not any(k in reach[v] and v not in reach[k] for v in methods)]
    return result, [k for k in methods if any(k in reach[v] for v in edges[k])]
