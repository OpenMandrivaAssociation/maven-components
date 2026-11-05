#!/usr/bin/python
"""Index the jars shipped by the maven package.

Maven's own RPM does not publish mvn() capabilities. Plugins compile
against those jars, so this package writes sysjar records that point at
/usr/share/maven/lib and POMs Maven can resolve offline.

A parent reactor POM is not installed. Each record is a flat POM: the
parent is removed and the coordinates from pom.properties are written on
the project. Test dependencies are removed. Every other dependency must
be one of these records or a jar cooker already packages. An unknown
coordinate fails the build instead of publishing a POM nothing can resolve.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

POM_NS = "http://maven.apache.org/POM/4.0.0"
ET.register_namespace("", POM_NS)

# group:artifact of jars cooker already provides. Optional dependencies are
# checked against this set too, but they are not RPM Requires.
KNOWN = {
    "com.google.code.gson:gson",
    "com.google.guava:failureaccess",
    "com.google.guava:guava",
    "com.google.inject:guice",
    "commons-cli:commons-cli",
    "commons-codec:commons-codec",
    "javax.annotation:javax.annotation-api",
    "javax.inject:javax.inject",
    "org.apache.httpcomponents:httpclient",
    "org.apache.httpcomponents:httpcore",
    "org.apache.maven.wagon:wagon-provider-api",
    "org.codehaus.plexus:plexus-cipher",
    "org.codehaus.plexus:plexus-classworlds",
    "org.codehaus.plexus:plexus-component-annotations",
    "org.codehaus.plexus:plexus-interpolation",
    "org.codehaus.plexus:plexus-sec-dispatcher",
    "org.codehaus.plexus:plexus-utils",
    "org.jline:jansi-core",
    "org.jline:jline-terminal",
    "org.jline:jline-terminal-jni",
    "org.ow2.asm:asm",
    "org.slf4j:jcl-over-slf4j",
    "org.slf4j:slf4j-api",
    "org.slf4j:slf4j-simple",
}

EXPECTED = {
    "org.apache.maven": "3.10.0",
    "org.apache.maven.resolver": "2.0.24",
}


def q(tag: str) -> str:
    return f"{{{POM_NS}}}{tag}"


def local(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def child(parent: ET.Element, tag: str) -> ET.Element | None:
    for node in list(parent):
        if local(node.tag) == tag:
            return node
    return None


def children(parent: ET.Element, tag: str) -> list[ET.Element]:
    return [node for node in list(parent) if local(node.tag) == tag]


def properties(jar: zipfile.ZipFile) -> dict[str, str]:
    names = [name for name in jar.namelist() if name.endswith("pom.properties") and name.startswith("META-INF/maven/")]
    if len(names) != 1:
        raise SystemExit(f"{jar.filename} has {len(names)} pom.properties")
    found = {}
    for line in jar.read(names[0]).decode("utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        found[key.strip()] = value.strip()
    return found


def pom_bytes(jar: zipfile.ZipFile, group: str, artifact: str) -> bytes:
    name = f"META-INF/maven/{group}/{artifact}/pom.xml"
    try:
        return jar.read(name)
    except KeyError as exc:
        raise SystemExit(f"{jar.filename} has no {name}") from exc


def minimal_pom(group: str, artifact: str, version: str, dependencies: list[dict]) -> ET.Element:
    root = ET.Element(q("project"))
    ET.SubElement(root, q("modelVersion")).text = "4.0.0"
    ET.SubElement(root, q("groupId")).text = group
    ET.SubElement(root, q("artifactId")).text = artifact
    ET.SubElement(root, q("version")).text = version
    ET.SubElement(root, q("packaging")).text = "jar"
    if dependencies:
        deps = ET.SubElement(root, q("dependencies"))
        for dep in dependencies:
            node = ET.SubElement(deps, q("dependency"))
            ET.SubElement(node, q("groupId")).text = dep["groupId"]
            ET.SubElement(node, q("artifactId")).text = dep["artifactId"]
            if dep.get("version"):
                ET.SubElement(node, q("version")).text = dep["version"]
            if dep.get("classifier"):
                ET.SubElement(node, q("classifier")).text = dep["classifier"]
            if dep.get("type") and dep["type"] != "jar":
                ET.SubElement(node, q("type")).text = dep["type"]
            if dep.get("scope") and dep["scope"] not in {"compile", ""}:
                ET.SubElement(node, q("scope")).text = dep["scope"]
            if dep.get("optional"):
                ET.SubElement(node, q("optional")).text = "true"
    return root


def dependencies_of(root: ET.Element) -> list[dict]:
    deps_node = child(root, "dependencies")
    if deps_node is None:
        return []
    found = []
    for dep in children(deps_node, "dependency"):
        group = text(child(dep, "groupId"))
        artifact = text(child(dep, "artifactId"))
        if not group or not artifact:
            continue
        scope = text(child(dep, "scope")) or "compile"
        if scope == "test":
            continue
        optional = text(child(dep, "optional")).lower() == "true"
        version = text(child(dep, "version"))
        # Bootstrap jars are named *.MBI. The packaged jar has the upstream version.
        if version.endswith(".MBI"):
            version = version[:-4]
        classifier = text(child(dep, "classifier"))
        # Maven's Guice jar is the "classes" classifier of the bootstrap build.
        # Cooker ships the normal guice jar.
        if classifier == "classes":
            classifier = ""
        found.append({
            "groupId": group,
            "artifactId": artifact,
            "version": version,
            "classifier": classifier,
            "type": text(child(dep, "type")) or "jar",
            "scope": scope,
            "optional": optional,
        })
    return found


def record(group: str, artifact: str, version: str, jar_path: str, pom_path: str, dependencies: list[dict]) -> dict:
    # RPM Requires follow non-optional compile and runtime dependencies.
    # Optional and provided dependencies stay in the POM for Maven, and are
    # not package dependencies: provided is not transitive, and an optional
    # jar is not required to compile a consumer.
    rpm_deps = []
    for dep in dependencies:
        if dep["optional"] or dep["scope"] in {"provided", "system", "test"}:
            continue
        rpm_deps.append({
            "groupId": dep["groupId"],
            "artifactId": dep["artifactId"],
            "version": dep["version"],
            "classifier": dep["classifier"],
            "type": dep["type"] or "jar",
            "scope": dep["scope"] or "compile",
            "optional": False,
        })
    return {
        "aliases": [],
        "artifactId": artifact,
        "classifier": "",
        "dependencies": rpm_deps,
        "extension": "jar",
        "groupId": group,
        "jar": jar_path,
        "pom": pom_path,
        "sysjar": 1,
        "version": version,
    }


def write_pom(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def write_record(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_known(group: str, artifact: str, dependencies: list[dict], indexed: set[str]) -> None:
    for dep in dependencies:
        coord = f"{dep['groupId']}:{dep['artifactId']}"
        if coord in indexed or coord in KNOWN:
            continue
        raise SystemExit(f"{group}:{artifact} depends on unpackaged {coord}")


def index_embedded(lib: Path, buildroot: Path, indexed: dict[str, dict]) -> None:
    for jar_path in sorted(lib.glob("*.jar")):
        with zipfile.ZipFile(jar_path) as jar:
            if not any(name.endswith("pom.properties") for name in jar.namelist()):
                continue
            props = properties(jar)
            group = props.get("groupId", "")
            artifact = props.get("artifactId", "")
            version = props.get("version", "")
            if group not in EXPECTED:
                continue
            if version != EXPECTED[group]:
                raise SystemExit(f"{jar_path.name} is {group}:{artifact}:{version}, expected {EXPECTED[group]}")
            root = ET.fromstring(pom_bytes(jar, group, artifact))
            deps = dependencies_of(root)
        coord = f"{group}:{artifact}"
        indexed[coord] = {
            "group": group,
            "artifact": artifact,
            "version": version,
            "jar": f"/usr/share/maven/lib/{jar_path.name}",
            "dependencies": deps,
        }


def add_synthetic(indexed: dict[str, dict], group: str, artifact: str, version: str, jar: str, dependencies: list[dict]) -> None:
    indexed[f"{group}:{artifact}"] = {
        "group": group,
        "artifact": artifact,
        "version": version,
        "jar": jar,
        "dependencies": dependencies,
    }


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit("usage: maven-components.py LIBDIR TOOLCHAINDIR BUILDROOT FILELIST")
    lib = Path(sys.argv[1])
    toolchain = Path(sys.argv[2])
    buildroot = Path(sys.argv[3])
    filelist_path = Path(sys.argv[4])
    if not lib.is_dir():
        raise SystemExit(f"maven lib dir not found: {lib}")
    indexed: dict[str, dict] = {}
    index_embedded(lib, buildroot, indexed)
    if "org.apache.maven:maven-core" not in indexed:
        raise SystemExit(f"maven-core jar not found under {lib}")
    if "org.apache.maven.resolver:maven-resolver-api" not in indexed:
        raise SystemExit(f"maven-resolver-api jar not found under {lib}")

    sisu_inject = lib / "org.eclipse.sisu.inject-0.9.0.M3.MBI.jar"
    sisu_plexus = lib / "org.eclipse.sisu.plexus-0.9.0.M3.MBI.jar"
    for path in (sisu_inject, sisu_plexus):
        if not path.is_file():
            raise SystemExit(f"missing {path}")
    add_synthetic(
        indexed,
        "org.eclipse.sisu",
        "org.eclipse.sisu.inject",
        "0.9.0.M3.MBI",
        f"/usr/share/maven/lib/{sisu_inject.name}",
        [
            {"groupId": "com.google.inject", "artifactId": "guice", "version": "", "classifier": "", "type": "jar", "scope": "compile", "optional": False},
            {"groupId": "javax.inject", "artifactId": "javax.inject", "version": "", "classifier": "", "type": "jar", "scope": "compile", "optional": False},
            {"groupId": "org.slf4j", "artifactId": "slf4j-api", "version": "", "classifier": "", "type": "jar", "scope": "compile", "optional": False},
        ],
    )
    add_synthetic(
        indexed,
        "org.eclipse.sisu",
        "org.eclipse.sisu.plexus",
        "0.9.0.M3.MBI",
        f"/usr/share/maven/lib/{sisu_plexus.name}",
        [
            {"groupId": "org.eclipse.sisu", "artifactId": "org.eclipse.sisu.inject", "version": "0.9.0.M3.MBI", "classifier": "", "type": "jar", "scope": "compile", "optional": False},
            {"groupId": "com.google.inject", "artifactId": "guice", "version": "", "classifier": "", "type": "jar", "scope": "compile", "optional": False},
            {"groupId": "javax.inject", "artifactId": "javax.inject", "version": "", "classifier": "", "type": "jar", "scope": "compile", "optional": False},
            {"groupId": "org.codehaus.plexus", "artifactId": "plexus-classworlds", "version": "", "classifier": "", "type": "jar", "scope": "compile", "optional": False},
            {"groupId": "org.codehaus.plexus", "artifactId": "plexus-component-annotations", "version": "", "classifier": "", "type": "jar", "scope": "compile", "optional": False},
            {"groupId": "org.codehaus.plexus", "artifactId": "plexus-utils", "version": "", "classifier": "", "type": "jar", "scope": "compile", "optional": False},
            {"groupId": "org.slf4j", "artifactId": "slf4j-api", "version": "", "classifier": "", "type": "jar", "scope": "compile", "optional": False},
        ],
    )

    annotations = sorted(toolchain.glob(
        "org/apache/maven/plugin-tools/maven-plugin-annotations/*/maven-plugin-annotations-*.jar"
    ))
    if len(annotations) != 1:
        raise SystemExit(f"expected one maven-plugin-annotations jar, found {annotations}")
    ann = annotations[0]
    # Directory is the version, 3.9.0.MBI. The jar is the JDK 27 toolchain copy.
    ann_version = ann.parent.name
    add_synthetic(
        indexed,
        "org.apache.maven.plugin-tools",
        "maven-plugin-annotations",
        ann_version,
        "/usr/share/maven/" + ann.relative_to(toolchain.parent).as_posix(),
        [],
    )

    names = set(indexed)
    for item in indexed.values():
        check_known(item["group"], item["artifact"], item["dependencies"], names)

    filelist = []
    groups = set()
    for coord in sorted(indexed):
        item = indexed[coord]
        stem = item["artifact"]
        pom_rel = f"/usr/share/maven-poms/{stem}.pom"
        index_rel = f"/usr/share/sysjar/index/{item['group']}/{stem}.sysjar"
        root = minimal_pom(item["group"], item["artifact"], item["version"], item["dependencies"])
        write_pom(buildroot / pom_rel.lstrip("/"), root)
        data = record(item["group"], item["artifact"], item["version"], item["jar"], pom_rel, item["dependencies"])
        write_record(buildroot / index_rel.lstrip("/"), data)
        if item["group"] not in groups:
            filelist.append(f"%dir /usr/share/sysjar/index/{item['group']}")
            groups.add(item["group"])
        filelist.append(index_rel)
        filelist.append(pom_rel)
    filelist_path.parent.mkdir(parents=True, exist_ok=True)
    filelist_path.write_text("".join(f"{line}\n" for line in filelist), encoding="utf-8")
    print(f"indexed {len(indexed)} artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
