import os
import sys
import subprocess
import getpass
import logging
import json
from distutils.spawn import find_executable
import pwd, grp

logger = logging.getLogger(__name__)

default_config = {
	'restart_supervisor_on_update': False,
	'auto_update': False,
	'serve_default_site': True,
	'rebase_on_pull': False,
	'update_bench_on_update': True,
	'frappe_user': getpass.getuser(),
	'shallow_clone': True
}

def get_frappe(bench='.'):
	frappe = os.path.abspath(os.path.join(bench, 'env', 'bin', 'frappe'))
	if not os.path.exists(frappe):
		print 'frappe app is not installed. Run the following command to install frappe'
		print 'bench get-app frappe https://github.com/frappe/frappe.git'
	return frappe

def init(path, apps_path=None, no_procfile=False, no_backups=False,
		no_auto_update=False, frappe_path=None, frappe_branch=None, wheel_cache_dir=None):
	from .app import get_app, install_apps_from_path
	if os.path.exists(path):
		print 'Directory {} already exists!'.format(path)
		sys.exit(1)

	os.mkdir(path)
	for dirname in ('apps', 'sites', 'config', 'logs'):
		os.mkdir(os.path.join(path, dirname))

	setup_logging()

	setup_env(bench=path)
	put_config(default_config, bench=path)
	if wheel_cache_dir:
		update_config({"wheel_cache_dir":wheel_cache_dir}, bench=path)
		prime_wheel_cache(bench=path)
	if not frappe_path:
		frappe_path = 'https://github.com/indictranstech/phr-frappe.git'
	get_app('frappe', frappe_path, branch=frappe_branch, bench=path)
	if not no_procfile:
		setup_procfile(bench=path)
	if not no_backups:
		setup_backups(bench=path)
	if not no_auto_update:
		setup_auto_update(bench=path)
	if apps_path:
		install_apps_from_path(apps_path, bench=path)

def exec_cmd(cmd, cwd='.'):
	try:
		subprocess.check_call(cmd, cwd=cwd, shell=True)
	except subprocess.CalledProcessError, e:
		print "Error:", getattr(e, "output", None) or getattr(e, "error", None)
		raise

def setup_env(bench='.'):
	exec_cmd('virtualenv -q {} -p {}'.format('env', sys.executable), cwd=bench)
	exec_cmd('./env/bin/pip -q install wheel', cwd=bench)
	exec_cmd('./env/bin/pip -q install https://github.com/frappe/MySQLdb1/archive/MySQLdb-1.2.5-patched.tar.gz', cwd=bench)

def setup_procfile(bench='.'):
	with open(os.path.join(bench, 'Procfile'), 'w') as f:
		f.write("""web: ./env/bin/frappe --serve --sites_path sites
worker: sh -c 'cd sites && exec ../env/bin/python -m frappe.celery_app worker'
workerbeat: sh -c 'cd sites && exec ../env/bin/python -m frappe.celery_app beat -s scheduler.schedule'""")

def new_site(site, mariadb_root_password=None, admin_password=None, bench='.'):
	logger.info('creating new site {}'.format(site))
	mariadb_root_password_fragment = '--root_password {}'.format(mariadb_root_password) if mariadb_root_password else ''
	admin_password_fragment = '--admin_password {}'.format(admin_password) if admin_password else ''
	exec_cmd("{frappe} --install {site} {site} {mariadb_root_password_fragment} {admin_password_fragment}".format(
				frappe=get_frappe(bench=bench),
				site=site,
				mariadb_root_password_fragment=mariadb_root_password_fragment,
				admin_password_fragment=admin_password_fragment
			), cwd=os.path.join(bench, 'sites'))
	if len(get_sites(bench=bench)) == 1:
		exec_cmd("{frappe} --use {site}".format(frappe=get_frappe(bench=bench), site=site), cwd=os.path.join(bench, 'sites'))

def patch_sites(bench='.'):
	exec_cmd("{frappe} --latest all".format(frappe=get_frappe(bench=bench)), cwd=os.path.join(bench, 'sites'))

def build_assets(bench='.'):
	exec_cmd("{frappe} --build".format(frappe=get_frappe(bench=bench)), cwd=os.path.join(bench, 'sites'))

def get_sites(bench='.'):
	sites_dir = os.path.join(bench, "sites")
	sites = [site for site in os.listdir(sites_dir) 
		if os.path.isdir(os.path.join(sites_dir, site)) and site not in ('assets',)]
	return sites

def get_sites_dir(bench='.'):
	return os.path.abspath(os.path.join(bench, 'sites'))

def get_bench_dir(bench='.'):
	return os.path.abspath(bench)

def setup_auto_update(bench='.'):
	# disabling auto update till Frappe version 5 is stable
	return
	logger.info('setting up auto update')
	add_to_crontab('0 10 * * * cd {bench_dir} &&  {bench} update --auto >> {logfile} 2>&1'.format(bench_dir=get_bench_dir(bench=bench),
		bench=os.path.join(get_bench_dir(bench=bench), 'env', 'bin', 'bench'),
		logfile=os.path.join(get_bench_dir(bench=bench), 'logs', 'auto_update_log.log')))

def setup_backups(bench='.'):
	logger.info('setting up backups')
	add_to_crontab('0 */6 * * * cd {sites_dir} &&  {frappe} --backup all >> {logfile} 2>&1'.format(sites_dir=get_sites_dir(bench=bench),
		frappe=get_frappe(bench=bench),
		logfile=os.path.join(get_bench_dir(bench=bench), 'logs', 'backup.log')))

def add_to_crontab(line):
	current_crontab = read_crontab()
	if not line in current_crontab:
		s = subprocess.Popen("crontab", stdin=subprocess.PIPE)
		s.stdin.write(current_crontab)
		s.stdin.write(line + '\n')
		s.stdin.close()

def read_crontab():
	s = subprocess.Popen(["crontab", "-l"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
	out = s.stdout.read()
	s.stdout.close()
	return out

def update_bench():
	logger.info('setting up sudoers')
	cwd = os.path.dirname(os.path.abspath(__file__))
	exec_cmd("git pull", cwd=cwd)

def setup_sudoers(user):
	sudoers_file = '/etc/sudoers.d/frappe'
	with open(sudoers_file, 'w') as f:
		f.write("{user} ALL=(ALL) NOPASSWD: {supervisorctl} restart frappe\:\n".format(
					user=user,
					supervisorctl=subprocess.check_output('which supervisorctl', shell=True).strip()))
	os.chmod(sudoers_file, 0440)

def setup_logging(bench='.'):
	if os.path.exists(os.path.join(bench, 'logs')):
		logger = logging.getLogger('bench')
		log_file = os.path.join(bench, 'logs', 'bench.log')
		formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
		hdlr = logging.FileHandler(log_file)
		hdlr.setFormatter(formatter)
		logger.addHandler(hdlr)
		logger.setLevel(logging.DEBUG)

def get_config(bench='.'):
	config_path = os.path.join(bench, 'config.json')
	if not os.path.exists(config_path):
		return {}
	with open(config_path) as f:
		return json.load(f)

def put_config(config, bench='.'):
	with open(os.path.join(bench, 'config.json'), 'w') as f:
		return json.dump(config, f, indent=1)

def update_config(new_config, bench='.'):
	config = get_config(bench=bench)
	config.update(new_config)
	put_config(config, bench=bench)

def get_program(programs):
	program = None
	for p in programs:
		program = find_executable(p)
		if program:
			break
	return program

def get_process_manager():
	return get_program(['foreman', 'forego', 'honcho'])
    
def start():
	program = get_process_manager()
	if not program:
		raise Exception("No process manager found")
	os.execv(program, [program, 'start'])

def check_cmd(cmd, cwd='.'):
	try:
		subprocess.check_call(cmd, cwd=cwd, shell=True)
		return True
	except subprocess.CalledProcessError, e:
		return False

def get_git_version():
	version = get_cmd_output("git --version")
	return version.strip().split()[-1]

def check_git_for_shallow_clone():
	git_version = get_git_version()
	if git_version.startswith('1.9') or git_version.startswith('2'):
		return True
	return False

def get_cmd_output(cmd, cwd='.'):
	try:
		return subprocess.check_output(cmd, cwd=cwd, shell=True)
	except subprocess.CalledProcessError, e:
		print "Error:", e.output
		raise

def restart_supervisor_processes(bench='.'):
	conf = get_config(bench=bench)
	cmd = conf.get('supervisor_restart_cmd', 'sudo supervisorctl restart frappe:')
	exec_cmd(cmd, cwd=bench)

def get_site_config(site, bench='.'):
	config_path = os.path.join(bench, 'sites', site, 'site_config.json')
	if not os.path.exists(config_path):
		return {}
	with open(config_path) as f:
		return json.load(f)

def put_site_config(site, config, bench='.'):
	config_path = os.path.join(bench, 'sites', site, 'site_config.json')
	with open(config_path, 'w') as f:
		return json.dump(config, f, indent=1)

def update_site_config(site, new_config, bench='.'):
	config = get_site_config(site, bench=bench)
	config.update(new_config)
	put_site_config(site, config, bench=bench)

def set_nginx_port(site, port, bench='.', gen_config=True):
	set_site_config_nginx_property(site, {"nginx_port": port}, bench=bench)

def set_ssl_certificate(site, ssl_certificate, bench='.', gen_config=True):
	set_site_config_nginx_property(site, {"ssl_certificate": ssl_certificate}, bench=bench)

def set_ssl_certificate_key(site, ssl_certificate_key, bench='.', gen_config=True):
	set_site_config_nginx_property(site, {"ssl_certificate_key": ssl_certificate_key}, bench=bench)

def set_nginx_port(site, port, bench='.', gen_config=True):
	set_site_config_nginx_property(site, {"nginx_port": port}, bench=bench)

def set_site_config_nginx_property(site, config, bench='.', gen_config=True):
	from .config import generate_nginx_config
	if site not in get_sites(bench=bench):
		raise Exception("No such site")
	update_site_config(site, config, bench=bench)
	if gen_config:
		generate_nginx_config()

def set_url_root(site, url_root, bench='.'):
	update_site_config(site, {"host_name": url_root}, bench=bench)

def set_default_site(site, bench='.'):
	if not site in get_sites(bench=bench):
		raise Exception("Site not in bench")
	exec_cmd("{frappe} --use {site}".format(frappe=get_frappe(bench=bench), site=site),
			cwd=os.path.join(bench, 'sites'))

def update_requirements(bench='.'):
	pip = os.path.join(bench, 'env', 'bin', 'pip')
	apps_dir = os.path.join(bench, 'apps')
	for app in os.listdir(apps_dir):
		req_file = os.path.join(apps_dir, app, 'requirements.txt')
		if os.path.exists(req_file):
			exec_cmd("{pip} install -q -r {req_file}".format(pip=pip, req_file=req_file))

def backup_site(site, bench='.'):
	exec_cmd("{frappe} --backup {site}".format(frappe=get_frappe(bench=bench), site=site),
			cwd=os.path.join(bench, 'sites'))

def backup_all_sites(bench='.'):
	for site in get_sites(bench=bench):
		backup_site(site, bench=bench)

def prime_wheel_cache(bench='.'):
	conf = get_config(bench=bench)
	wheel_cache_dir = conf.get('wheel_cache_dir')
	if not wheel_cache_dir:
		raise Exception("Wheel cache dir not configured")
	requirements = os.path.join(os.path.dirname(__file__), 'templates', 'cached_requirements.txt')
	cmd =  "{pip} wheel --find-links {wheelhouse} --wheel-dir {wheelhouse} -r {requirements}".format(
				pip=os.path.join(bench, 'env', 'bin', 'pip'),
				wheelhouse=wheel_cache_dir,
				requirements=requirements)
	exec_cmd(cmd)

def is_root():
	if os.getuid() == 0:
		return True
	return False

def set_mariadb_host(host, bench='.'):
	update_common_site_config({'db_host': host}, bench=bench)

def update_common_site_config(ddict, bench='.'):
	update_json_file(os.path.join(bench, 'sites', 'common_site_config.json'), ddict)

def update_json_file(filename, ddict):
	with open(filename, 'r') as f:
		content = json.load(f)
	content.update(ddict)
	with open(filename, 'w') as f:
		content = json.dump(content, f, indent=1)

def drop_privileges(uid_name='nobody', gid_name='nogroup'):
	# from http://stackoverflow.com/a/2699996
	if os.getuid() != 0:
		# We're not root so, like, whatever dude
		return

	# Get the uid/gid from the name
	running_uid = pwd.getpwnam(uid_name).pw_uid
	running_gid = grp.getgrnam(gid_name).gr_gid

	# Remove group privileges
	os.setgroups([])

	# Try setting the new uid/gid
	os.setgid(running_gid)
	os.setuid(running_uid)

	# Ensure a very conservative umask
	old_umask = os.umask(077)

def fix_file_perms(frappe_user=None):
	files = [
	"logs/web.error.log",
	"logs/web.log",
	"logs/workerbeat.error.log",
	"logs/workerbeat.log",
	"logs/worker.error.log",
	"logs/worker.log",
	"config/nginx.conf",
	"config/supervisor.conf",
	]

	if not frappe_user:
		frappe_user = get_config().get('frappe_user')

	if not frappe_user:
		print "frappe user not set"
		sys.exit(1)

	for path in files:
		if os.path.exists(path):
			uid = pwd.getpwnam(frappe_user).pw_uid
			gid = grp.getgrnam(frappe_user).gr_gid
			os.chown(path, uid, gid)
