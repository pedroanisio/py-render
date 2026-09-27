<?xml version="1.0" encoding="UTF-8"?>
<schema xmlns="http://purl.oclc.org/dsdl/schematron" queryBinding="xslt">
  <title>Scene-render 1.1 cross-field constraints</title>
  <!-- These rules implement the explicit cross-field contracts in the XSD.
       The 1.0 version gate requires the authoritative 1.0 element inventory;
       it is not inferred from the order or appearance of the 1.1 declarations. -->
  <pattern id="text-source">
    <rule context="scene/assets/text">
      <assert id="SR-TEXT-SOURCE" test="boolean(@text) != boolean(span)">Text content must come from exactly one of @text or span children (textAssetType).</assert>
    </rule>
  </pattern>
  <pattern id="pin-body">
    <rule context="scene/physics/constraint[@type='pin']">
      <assert id="SR-PIN-BODY" test="not(@b)">A pin constraint ties body a to a world point; body b is not allowed (physicsConstraintType).</assert>
    </rule>
  </pattern>
  <pattern id="transcription-cache">
    <rule context="scene/captions/captionTrack[@transcribe or @cache or @cacheSha256]">
      <assert id="SR-TRANSCRIPTION-CACHE" test="@cache and @cacheSha256">Transcription caches require both @cache and @cacheSha256 so rendering can verify their content (captionTrackType).</assert>
    </rule>
  </pattern>
  <pattern id="image-sequence-range">
    <rule context="scene/assets/imageSequence">
      <assert id="SR-IMAGE-SEQUENCE-RANGE" test="number(@last) &gt;= number(@first)">An image sequence must end at or after its first frame; @step is positive (imageSequenceAssetType).</assert>
    </rule>
  </pattern>
</schema>
