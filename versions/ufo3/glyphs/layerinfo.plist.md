---
layout: default
title: layerinfo.plist
---

{: .fileformat}
| **File Format** | [XML Property List](http://www.apple.com/DTDs/PropertyList-1.0.dtd) |

This file contains information about the layer. This file is optional. Not all values are required for a proper file.

## Specification

The property list data consists of a dictionary at the top level. The keys and values are as follows.

{: .name-type-description-default}
| key | type | description | default |
|--|--|--|--|
| color | [color definition] | The color that should be used for all glyphs in the layer. This attribute is optional. | None |
| lib | dictionary | A lib specific to the layer. To avoid naming conflicts, keys should use the [Reverse Domain Naming Scheme] defined for lib.plist. | None |

  [color definition]: ../../conventions/#colors
  [reverse domain naming scheme]: ../../conventions/#reverse-domain-naming-schemes