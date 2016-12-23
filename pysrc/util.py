import functools

def cmp_sort(lst, cmp):
    key = functools.cmp_to_key(cmp)
    lst.sort(key=key)
