%global resolver_api_ver 1.9.16

Name:		maven-components
Version:	3.10.0
Release:	3
Summary:	Maven and Resolver jars as system Maven artifacts
Group:		Development/Java
License:	Apache-2.0
URL:		https://maven.apache.org/
BuildArch:	noarch

Source0:	maven-components.py
# Tag maven-resolver-1.9.16. Only the API module is compiled. Gradle
# 9.9.1 strictly pins org.apache.maven.resolver:maven-resolver-api here.
Source1:	https://github.com/apache/maven-resolver/archive/refs/tags/maven-resolver-%{resolver_api_ver}.tar.gz#/maven-resolver-%{resolver_api_ver}.tar.gz

BuildRequires:	python
BuildRequires:	sysjar
BuildRequires:	java-current-devel
BuildRequires:	maven >= 1:3.10.0-2
Requires:	maven >= 1:3.10.0-2
# guice, jcl-over-slf4j and plexus-component-annotations inherit these
# parents. The jar packages do not ship the parent POMs.
Requires:	mvn(com.google.inject:guice-parent:pom::)
Requires:	mvn(org.slf4j:slf4j-parent:pom::)
Requires:	mvn(org.codehaus.plexus:plexus-containers:pom::)

%description
Index of the jars in the maven package. One record per Maven coordinate
under /usr/share/sysjar, pointing at the jar Maven already ships. Plugins
can compile against maven-core, the plugin API and Resolver without a
second copy of those jars. maven-resolver-api %{resolver_api_ver} is
compiled from that tag and installed beside the 2.0.24 API jar.

%prep
%setup -q -T -c -n %{name}-%{version} -a 1
find . \( -name '*.jar' -o -name '*.class' -o -name '*.so' -o -name '*.node' -o -name '*.wasm' \) -delete

%build
set -eo pipefail
export JAVA_HOME=%{_jvmdir}/java-openjdk
export PATH="$JAVA_HOME/bin:$PATH"
api=maven-resolver-maven-resolver-%{resolver_api_ver}/maven-resolver-api
test -d "$api/src/main/java"
find "$api/src/main/java" -name '*.java' > sources.list
test "$(wc -l < sources.list)" -eq 129
mkdir -p classes
javac --release 8 -encoding UTF-8 -Xlint:-options -proc:none \
	-d classes @sources.list
printf 'Manifest-Version: 1.0\nAutomatic-Module-Name: org.apache.maven.resolver\n\n' > manifest.mf
jar cfm maven-resolver-api.jar manifest.mf -C classes .
jar i maven-resolver-api.jar
javap -v -classpath classes org.eclipse.aether.artifact.DefaultArtifact | grep -F -q 'major version: 52'
cat > maven-resolver-api.pom << EOF
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>org.apache.maven.resolver</groupId>
  <artifactId>maven-resolver-api</artifactId>
  <version>%{resolver_api_ver}</version>
  <name>Maven Artifact Resolver API</name>
  <description>API for Maven Resolver %{resolver_api_ver}.</description>
  <url>https://maven.apache.org/resolver/</url>
  <licenses>
    <license>
      <name>Apache-2.0</name>
      <url>https://www.apache.org/licenses/LICENSE-2.0.txt</url>
    </license>
  </licenses>
</project>
EOF

%install
set -eo pipefail
python %{SOURCE0} %{_datadir}/maven/lib %{_datadir}/maven/toolchain %{buildroot} %{_builddir}/maven-components.files
artifact=maven-resolver-api
group=org.apache.maven.resolver
index="%{buildroot}/usr/share/sysjar/index/${group}/${artifact}.sysjar"
saved=$(mktemp)
cp -a "$index" "$saved"
sysjar install --buildroot %{buildroot} \
	--artifact "maven-resolver-api.pom:maven-resolver-api.jar" \
	--file-name "${artifact}-%{resolver_api_ver}" \
	--filelist .mfiles-resolver-1916
/bin/mv "$index" "%{buildroot}/usr/share/sysjar/index/${group}/${artifact}-%{resolver_api_ver}.sysjar"
/bin/cp -a "$saved" "$index"
rm -f "$saved"
sed -i "s|/usr/share/sysjar/index/${group}/${artifact}\\.sysjar|/usr/share/sysjar/index/${group}/${artifact}-%{resolver_api_ver}.sysjar|" .mfiles-resolver-1916
grep -F -q '"version": "2.0.24"' "$index"
grep -F -q '"version": "%{resolver_api_ver}"' "%{buildroot}/usr/share/sysjar/index/${group}/${artifact}-%{resolver_api_ver}.sysjar"
while IFS= read -r line; do
	grep -qxF -- "$line" %{_builddir}/maven-components.files || printf '%s\n' "$line" >> %{_builddir}/maven-components.files
done < .mfiles-resolver-1916

%check
set -eo pipefail
export JAVA_HOME=%{_jvmdir}/java-openjdk
export PATH="$JAVA_HOME/bin:$PATH"
JAR=%{buildroot}%{_javadir}/maven-resolver-api-%{resolver_api_ver}.jar
test -s "$JAR"
mkdir -p check
cat > check/Smoke.java << 'EOF'
import org.eclipse.aether.artifact.DefaultArtifact;
public class Smoke {
	public static void main(String[] args) {
		DefaultArtifact artifact = new DefaultArtifact("org.apache.maven.resolver:maven-resolver-api:1.9.16");
		System.out.println("resolver1916_ok " + artifact.getGroupId() + " " + artifact.getArtifactId() + " " + artifact.getVersion());
	}
}
EOF
javac --release 8 -encoding UTF-8 -proc:none -cp "$JAR" -d check check/Smoke.java
java -cp "check:$JAR" Smoke > check/check.log
cat check/check.log
grep -F -q 'resolver1916_ok org.apache.maven.resolver maven-resolver-api 1.9.16' check/check.log

%files -f %{_builddir}/maven-components.files
