#! /usr/bin/env python
from __future__ import print_function

import os
import re
import subprocess
import sys
from collections import OrderedDict, defaultdict
from configparser import ConfigParser
from pathlib import Path

import click
import jinja2
import m2r
from pkg_resources import parse_version

__version__ = "0.1.1"

CHANGELOG = """
# Change Log
All notable changes to landlab will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

This file was auto-generated using `scripts/make_changelog.py`.

{% for tag, sections in releases.items() %}
## Version {{ tag }}
*(released on {{ release_date[tag] }})*

{% for section, changes in sections.items() %}
### {{section}}
{% for change in changes -%}
* {{ change }}
{% endfor -%}
{% endfor -%}
{% endfor -%}
""".strip()

SECTIONS = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]


class PullRequest(object):
    PR_REGEX = "Merge pull request (?P<pr>#[0-9]+) from (?P<branch>[\S]*)(?P<postscript>[\s\S]*$)"

    def __init__(self, commit):
        self.commit = commit

        pr = None
        for line in self.raw_body.splitlines():
            pr = self._parse_pr_string(line)
            if pr is not None:
                break
        pr = pr or defaultdict(lambda: None)
        self.pr = pr["pr"]
        self.branch = pr["branch"]
        self.postscript = pr["postscript"]

        self.summary = None
        for line in self.raw_body.splitlines():
            pr = self._parse_pr_string(line)
            if not pr:
                self.summary = line.strip()
                break

    def __str__(self):
        if self.pr:
            summary = "{summary} [[#{pr}](https://github.com/csdms/pymt/pull/{pr})]".format(
                summary=self.summary, pr=self.pr[1:]
            )
        else:
            summary = "{summary}".format(summary=self.summary)
        return summary

    def topic(self):
        summary = self.summary
        if summary.startswith("Add"):
            return "Added"
        elif summary.startswith("Deprecate"):
            return "Deprecated"
        elif summary.startswith("Remove"):
            return "Removed"
        elif summary.startswith("Fix"):
            return "Fixed"
        elif summary.startswith("Security"):
            return "Security"
        else:
            return "Changed"

    def _parse_pr_string(self, line):
        m = re.match(PullRequest.PR_REGEX, line)
        try:
            return m.groupdict()
        except AttributeError:
            return None

    @property
    def raw_body(self):
        cmd = ["git", "log", "-1", "-U", "--format=%B", self.commit]
        return subprocess.check_output(cmd).strip().decode("utf-8")

    @property
    def subject(self):
        cmd = ["git", "log", "-1", "-U", "--format=%s", self.commit]
        return subprocess.check_output(cmd).strip().decode("utf-8")

    @property
    def body(self):
        cmd = ["git", "log", "-1", "-U", "--format=%b", self.commit]
        return subprocess.check_output(cmd).strip().decode("utf-8")


def git_list_merges(start=None, stop="HEAD"):
    cmd = ["git", "log", "--merges", "--format=%H"]
    if start:
        cmd.append("{start}..{stop}".format(start=start, stop=stop))
    return subprocess.check_output(cmd).strip().decode("utf-8").splitlines()


def git_log(start=None, stop="HEAD"):
    cmd = [
        "git",
        "log",
        # "--first-parent",
        # "master",
        "--merges",
        # "--topo-order",
        # '--pretty=message: %s+author:%an+body: %b'],
        # "--pretty=%s [%an]",
        "--pretty=%s",
        # '--oneline',
    ]
    if start:
        cmd.append("{start}..{stop}".format(start=start, stop=stop))
    return subprocess.check_output(cmd).strip().decode("utf-8")


def git_tag():
    return subprocess.check_output(["git", "tag"]).strip().decode("utf-8")


def git_tag_date(tag):
    return (
        subprocess.check_output(["git", "show", tag, "--pretty=%ci"])
        .strip()
        .split()[0]
        .decode("utf-8")
    )


def git_top_level():
    return (
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"])
        .strip()
        .decode("utf-8")
    )


def releases(ascending=True):
    tags = git_tag().splitlines() + ["HEAD"]
    if ascending:
        return tags
    else:
        return tags[::-1]


def render_changelog(format="rst"):
    tags = releases(ascending=False)

    pull_requests = defaultdict(list)
    release_date = dict()
    for start, stop in zip(tags[1:], tags[:-1]):
        if stop.startswith("v"):
            version = ".".join(parse_version(stop[1:]).base_version.split(".")[:2])
        else:
            version = stop
        for commit in git_list_merges(start=start, stop=stop):
            pull_requests[version].append(PullRequest(commit))

        release_date[version] = git_tag_date(stop)

    changelog = OrderedDict()
    for version, pulls in pull_requests.items():
        groups = defaultdict(list)
        for pull in pulls:
            groups[pull.topic()].append(str(pull))
        changelog[version] = groups

    env = jinja2.Environment(loader=jinja2.DictLoader({"changelog": CHANGELOG}))
    contents = env.get_template("changelog").render(
        releases=changelog, release_date=release_date
    )
    if format == "rst":
        contents = m2r.convert(contents)
    return contents


def read_setup_cfg(ctx, param, value):
    assert not isinstance(value, (int, bool)), "Invalid parameter type passed"
    if not value:
        path = Path(git_top_level()) / "setup.cfg"
        if path.is_file():
            value = str(path)
        else:
            return None

    setup_cfg = ConfigParser()
    setup_cfg.read(value)

    if not setup_cfg.has_section("tool:changelog"):
        return None

    if ctx.default_map is None:
        ctx.default_map = {}
    ctx.default_map.update(  # type: ignore  # bad types in .pyi
        {
            k.replace("--", "").replace("-", "_"): v
            for k, v in setup_cfg.items("tool:changelog")
        }
    )
    return value


@click.command()
@click.pass_context
@click.argument("output", type=click.File("w"), default="-")
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help=(
        "Don't emit non-error messages to stderr. Errors are still emitted, "
        "silence those with 2>/dev/null."
    ),
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help=(
        "Also emit messages to stderr about files that were not changed or were "
        "ignored due to --exclude=."
    ),
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Overwrite an existing change log without prompting.",
)
@click.version_option(version=__version__)
@click.option(
    "--format",
    type=click.Choice(["md", "rst"]),
    default="rst",
    help="Format to use for the CHANGELOG.",
)
@click.option("--batch", is_flag=True, help="Run in batch mode.")
@click.option(
    "--config",
    type=click.Path(
        exists=False, file_okay=True, dir_okay=False, readable=True, allow_dash=False
    ),
    is_eager=True,
    callback=read_setup_cfg,
    help="Read configuration from PATH.",
)
def main(ctx, output, quiet, verbose, format, force, batch, config):

    contents = render_changelog(format=format)

    path_to_changelog = os.path.join(git_top_level(), "CHANGELOG." + format)

    if not batch and not quiet:
        click.echo_via_pager(contents)
        click.confirm("Looks good?", abort=True)

    if os.path.isfile(path_to_changelog) and not force:
        if not batch and not click.confirm(
            "Overwrite existing {0}?".format(path_to_changelog)
        ):
            click.secho(
                "{0} exists. Use --force to overwrite".format(path_to_changelog),
                fg="red",
                err=True,
            )
            sys.exit(1)

    with open(path_to_changelog, "w") as fp:
        fp.write(contents)
    click.secho(
        "Fresh change log at {0}".format(path_to_changelog), bold=True, err=True
    )


if __name__ == "__main__":
    main(auto_envvar_prefix="CHANGELOG")
