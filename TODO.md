TODOs
=====

### User facing improvements:
- previews broken if using optional fonts (seven-segment, fourteen-segment, nixie) 
  - mitigate by generating previews on an actual raspberry pi with the fonts installed
- previews assume a display size and orientation, which have now been abstracted away /-:
- add ability for user to change their display driver/profile without doing the entire installation again

### Internal / developer improvements:
- tighten up mypy type errors and warnings
- same for ruff suggestions
