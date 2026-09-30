# Local Reference PDFs

Store legally accessible local copies of cited sources here when useful for
research workflow or agent context.

Recommended convention:

```bibtex
@article{citation-key,
  ...
  file = {references/citation-key.pdf}
}
```

The `file` path is relative to this paper directory, because the manuscript is
built from that directory. Local reference links are hidden by default in the
bibliography. Uncomment `\showlocalreferences` in `main.tex` to show them.

Keep licensed or private PDFs out of git unless redistribution is explicitly
allowed.
