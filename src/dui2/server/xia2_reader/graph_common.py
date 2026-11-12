# pieces shared by the scripts that build the command dependency graph,
# no matter which file they read (timing_data.json or xia2-debug.txt)
import os, json, shutil, subprocess

if __package__:
    from dui2.server.data_n_json import spit_out

else:
    def spit_out(str_out = " None ", req_obj = None, out_type = None):
        print(str_out, end = "")



# parts of a parameter name (before "=") that mark it as a written file
# e.g.: output.experiments=..., mtz.hklout=..., json=...
OUTPUT_KEY_PARTS = ["output", "hklout", "json"]

# CCP4 style positional pairs, e.g.: freerflag 'hklin' 'a.mtz' 'hklout' 'b.mtz'
POSITIONAL_IN = ["hklin", "xyzin"]
POSITIONAL_OUT = ["hklout", "xyzout"]

FILE_EXTENSIONS = [
    ".expt", ".refl", ".mtz", ".mmcif", ".cif", ".json", ".phil",
    ".mask", ".sca", ".p4p", ".html", ".log", ".bz2",
]

# parameters whose value is a result of a previous program, not a file,
# (parameter name, programs whose most recent run gave that value)
PARAM_FROM_PROGRAM = [
    (
        "change_of_basis_op",
        ["dials.refine_bravais_settings", "dials.symmetry"],
    ),
    ("space_group", ["dials.refine_bravais_settings", "dials.symmetry"]),
    ("cut_data.d_min", ["dials.estimate_resolution"]),
]


def reversed_find_str(str_in = None, lst_sep_lst = ["=", os.sep]):
    # position of the last separator, -1 if there is none
    cut = -1
    for sep in lst_sep_lst:
        cut = max(cut, str_in.rfind(sep))

    return str_in[cut + 1:]


def is_dials_command(exe_cmd):
    # filtering and allowing only dials commands to pass
    program_name = reversed_find_str(str_in = exe_cmd, lst_sep_lst = [os.sep])
    DO_starts_with_dials = bool(program_name.startswith("dials."))
    return DO_starts_with_dials


def is_output_key(key):
    if key.endswith("hklout"):
        return True

    for part in key.split("."):
        if part in OUTPUT_KEY_PARTS:
            return True

    return False


def has_file_extension(file_name):
    for ext in FILE_EXTENSIONS:
        if file_name.endswith(ext):
            return True

    return False


def is_bravais_setting(file_name):
    prefix, suffix = "bravais_setting_", ".expt"
    if not (file_name.startswith(prefix) and file_name.endswith(suffix)):
        return False

    return file_name[len(prefix):-len(suffix)].isdecimal()


def is_bravais_summary(file_name):
    return file_name == "bravais_summary.json"


# files written with default names that never appear as output parameters,
# (test on the file name, program that wrote it)
IMPLICIT_OUTPUTS = [
    (is_bravais_setting, "dials.refine_bravais_settings"),
    (is_bravais_summary, "dials.refine_bravais_settings"),
]


def find_implicit_parent(file_name, prev_cmd_lst):
    for matches, exe_cmd in IMPLICIT_OUTPUTS:
        if matches(file_name):
            # most recent previous run of that program
            for prev_dict in reversed(prev_cmd_lst):
                if prev_dict['exe_cmd'] == exe_cmd:
                    return prev_dict['curr_poss']

    return None


def find_param_parent(single_par, prev_cmd_lst):
    for key, exe_cmd_lst in PARAM_FROM_PROGRAM:
        if single_par.startswith(key + "="):
            # most recent previous run of any of those programs
            for prev_dict in reversed(prev_cmd_lst):
                if prev_dict['exe_cmd'] in exe_cmd_lst:
                    return prev_dict['curr_poss']

    return None


def short_label(label):
    # parameter values keep their name, files lose path and "key="
    for key, exe_cmd_lst in PARAM_FROM_PROGRAM:
        if label.startswith(key + "="):
            return label

    return reversed_find_str(str_in = label)


def find_work_dirs(list_of_commands):
    # sets cmd_dict['work_dir'], the directory where each command ran,
    # using only directories that the logged command lines imply,
    # it stays None when the log does not tell it
    #
    # xia2 removes "<working directory>/" from the arguments it logs, so:
    #  - a file read from another directory keeps its full path, e.g.:
    #      dials.index  ... 'output.experiments=5_indexed.expt'
    #      dials.refine '/full/path/.../index/5_indexed.expt' ...
    #    tells that dials.index ran in /full/path/.../index

    dir_of_file, ambiguous_set = map_abs_paths(list_of_commands)
    conflict_lst = spread_work_dirs(
        list_of_commands, dir_of_file, ambiguous_set
    )
    common_dir = report_n_common_dir(list_of_commands, conflict_lst)
    fill_missing_work_dirs(list_of_commands, common_dir)


def map_abs_paths(list_of_commands):
    dir_of_file = {}
    ambiguous_set = set()
    for cmd_dict in list_of_commands:
        cmd_dict['work_dir'] = None
        for single_par in cmd_dict['par_lst']:
            path_str = reversed_find_str(
                str_in = single_par, lst_sep_lst = ["="]
            )
            if os.path.isabs(path_str):
                file_name = reversed_find_str(str_in = path_str)
                dir_name = os.path.dirname(os.path.normpath(path_str))
                if file_name in ambiguous_set:
                    continue

                elif file_name not in dir_of_file:
                    dir_of_file[file_name] = dir_name

                elif dir_of_file[file_name] != dir_name:
                    del dir_of_file[file_name]
                    ambiguous_set.add(file_name)

    return dir_of_file, ambiguous_set


def spread_work_dirs(list_of_commands, dir_of_file, ambiguous_set):
    conflict_lst = []
    found_new = True
    while found_new:
        found_new = False
        for cmd_dict in list_of_commands:
            if cmd_dict['work_dir'] is not None:
                continue

            no_path_lst = []
            for single_par in cmd_dict['par_lst']:
                path_str = reversed_find_str(
                    str_in = single_par, lst_sep_lst = ["="]
                )
                if has_file_extension(path_str) and os.sep not in path_str:
                    no_path_lst.append(path_str)

            # every directory the files of this command point to
            dir_set = set()
            for file_name in no_path_lst:
                if file_name in dir_of_file:
                    dir_set.add(dir_of_file[file_name])

            if len(dir_set) == 1:
                new_work_dir = dir_set.pop()
                #print("new_work_dir =", new_work_dir)
                cmd_dict['work_dir'] = new_work_dir
                found_new = True
                for file_name in no_path_lst:
                    if (
                        file_name not in dir_of_file
                        and file_name not in ambiguous_set
                    ):
                        dir_of_file[file_name] = cmd_dict['work_dir']

            elif len(dir_set) > 1 and cmd_dict not in conflict_lst:
                conflict_lst.append(cmd_dict)

    return conflict_lst


def report_n_common_dir(list_of_commands, conflict_lst):
    common_dir = None
    for cmd_dict in list_of_commands:
        if cmd_dict['work_dir'] is None:
            # not implied by the log, left as None on purpose
            if cmd_dict in conflict_lst:
                print("Dir not found (conflicting dirs) for:",
                      cmd_dict['curr_poss'], cmd_dict['exe_cmd'])

            else:
                tmp_off = '''print("Dir not found for:",
                      cmd_dict['curr_poss'], cmd_dict['exe_cmd']
                )'''

        elif common_dir is None:
            common_dir = cmd_dict['work_dir']

        else:
            common_dir = os.path.commonpath([common_dir, cmd_dict['work_dir']])


    return common_dir

def fill_missing_work_dirs(list_of_commands, common_dir):
    print("\n using:", common_dir, "as root path")
    natv_posi = common_dir.rfind("NATIVE") - 1
    extra_dir_left = common_dir[0: natv_posi]

    for cmd_dict in list_of_commands:
        if cmd_dict['work_dir'] is None:
            cmd_dict['work_dir'] = extra_dir_left + "/scale/"


# DUI2 grows its tree one step after another: a new node continues from the
# node it was launched from and inherits from it (and, through it, from all
# older nodes) every file it does not write itself, see CmdNode.__init__ and
# CmdNode.set_exe_files_out in multi_node.py, so in DUI2 only
# dials.combine_experiments has more than one parent
#
# the parents found through files are not like that, e.g.: dials.index reads
# imported.expt (from dials.import) and strong.refl (from dials.find_spots),
# so when a command has more than one parent only the last one is kept

JOIN_COMMANDS = ["dials.combine_experiments"]


def to_dui2_tree(list_of_commands, root_poss):
    # must run after the file connections (parent_pos_lst) are set, it
    # replaces 'parent_pos_lst' and 'chidren_pos_lst', the file connections
    # stay in 'file_parent_pos_lst'
    cmd_by_poss = {}
    for cmd_dict in list_of_commands:
        cmd_dict['file_parent_pos_lst'] = list(cmd_dict['parent_pos_lst'])
        cmd_dict['chidren_pos_lst'] = []
        cmd_by_poss[cmd_dict['curr_poss']] = cmd_dict

        if cmd_dict['parent_pos_lst'] == []:
            # e.g.: dials.import, hangs from the node that imported xia2
            cmd_dict['parent_pos_lst'] = [root_poss]

        elif cmd_dict['exe_cmd'] not in JOIN_COMMANDS:
            cmd_dict['parent_pos_lst'] = [max(cmd_dict['parent_pos_lst'])]

    for cmd_dict in list_of_commands:
        for parent_poss in cmd_dict['parent_pos_lst']:
            if parent_poss in cmd_by_poss:
                cmd_by_poss[parent_poss]['chidren_pos_lst'].append(
                    cmd_dict['curr_poss']
                )


def full_path_lst(file_lst, cmd_dict):
    # file names (no path) of a command -> the same files with full path,
    # as written in the command if it was there, otherwise in 'work_dir',
    # without 'work_dir' the name stays as it is (no path)
    path_lst = []
    for file_name in file_lst:
        if cmd_dict['work_dir'] is None:
            new_path = file_name

        else:
            new_path = os.path.join(cmd_dict['work_dir'], file_name)

        for single_par in cmd_dict['par_lst']:
            path_str = reversed_find_str(str_in = single_par, lst_sep_lst = ["="])
            if (
                os.path.isabs(path_str)
                and reversed_find_str(str_in = path_str) == file_name
            ):
                new_path = path_str
                break

        path_lst.append(os.path.normpath(new_path))

    return path_lst


def print_graph_table(list_of_commands):
    # one row per connection, same format for every input file type
    print("\n", "=" * 90)
    print("from", "parent", "to", "child", "via")
    print("-" * 90)
    for cmd_dict in list_of_commands:
        for parent_poss in sorted(cmd_dict['parent_pos_lst']):
            lbl_lst = []
            FIXME = '''
            fmt_str = "%-5s %-30s %-5s %-30s %s"
            for label in cmd_dict['files_from_parent_dict'][parent_poss]:
                lbl_lst.append(short_label(label))

            print(fmt_str % (
                parent_poss, list_of_commands[parent_poss]['exe_cmd'],
                cmd_dict['curr_poss'], cmd_dict['exe_cmd'],
                ", ".join(sorted(lbl_lst)),
            ))
            '''
            fmt_str = "%-5s %-30s %-5s %-30s"
            print(fmt_str % (
                parent_poss, list_of_commands[parent_poss]['exe_cmd'],
                cmd_dict['curr_poss'], cmd_dict['exe_cmd']
            ))

    print("=" * 90)


def export_reusable_graph_list(all_dat):
    with open("run_data", "w") as fp:
        json.dump(all_dat, fp, indent=4)


def build_reusable_graph_list(
    list_of_commands = None, bigger_lin_in = 0, import_lin = 0
):
    lst_nod = []
    bigger_lin = bigger_lin_in
    for cmd_dict in list_of_commands:
        if bigger_lin < cmd_dict['curr_poss']:
            bigger_lin = cmd_dict['curr_poss']

        parent_node_lst = cmd_dict['parent_pos_lst']

        if  parent_node_lst == []:
            parent_node_lst = [bigger_lin - 1]
            print("parent_node_lst(fixed) =", parent_node_lst)

        lst_expt_in = full_path_lst(cmd_dict['expt_from_prev_lst'], cmd_dict)
        lst_refl_in = full_path_lst(cmd_dict['refl_from_prev_lst'], cmd_dict)
        lst_expt_out = full_path_lst(cmd_dict['expt_for_next_lst'], cmd_dict)
        lst_refl_out = full_path_lst(cmd_dict['refl_for_next_lst'], cmd_dict)
        # like CmdNode.set_exe_files_out, a step that writes no .expt/.refl
        # hands on what it read, so a DUI2 step launched from it gets files
        if lst_expt_out == []:
            lst_expt_out = list(lst_expt_in)

        if lst_refl_out == []:
            lst_refl_out = list(lst_refl_in)

        tun_par_lst = cmd_dict['tuning_params_lst']
        tun_par_lst = [cmd_dict['exe_cmd']] + tun_par_lst
        lst2run = [tun_par_lst]
        node = {
            "_base_dir"             :os.getcwd(),
            "cmd_dict_ini"          :{
                                        "nod_lst":[None],
                                        "cmd_lst":[[None]]
                                    },
            "full_cmd_lst"          :cmd_dict['exe_cmd'],
            "lst2run"               :lst2run,
            "_lst_expt_in"          :lst_expt_in,
            "_lst_refl_in"          :lst_refl_in,
            "_lst_expt_out"         :lst_expt_out,
            "_lst_refl_out"         :lst_refl_out,
            "_run_dir"              :cmd_dict['work_dir'],

            "_html_rep"             :None,
            "_predic_refl"          :None,
            "log_file_path"         :cmd_dict.get('log_file_path'),
            "number"                :cmd_dict['curr_poss'],
            "parent_node_lst"       :parent_node_lst,
            "child_node_lst"        :cmd_dict['chidren_pos_lst'],
            "status"                :"Succeeded"
        }
        lst_nod.append(node)


    all_dat = {
            "step_list"             :lst_nod,
            "bigger_lin"            :bigger_lin,
    }
    return all_dat



def send_line(new_line, req_obj):
    # like add_log_line in multi_node, returns 1 when the client is gone
    try:
        spit_out(str_out = new_line, req_obj = req_obj, out_type = 'utf-8')

    except BrokenPipeError:
        return 1

    return 0


def run_n_spit_out(lst_dat_in, run_dir, req_obj):
    # runs one command in run_dir, sending each line it prints as it comes,
    # returns the number of lines that could not be sent (BrokenPipe)
    n_broken_pipes = 0
    exe_path = shutil.which(lst_dat_in[0])
    if exe_path is None:
        new_line = "command not found: " + str(lst_dat_in[0]) + "\n"
        return send_line(new_line, req_obj)

    lst_dat_in[0] = str(exe_path)
    run_proc = subprocess.Popen(
        lst_dat_in,
        shell = False,
        cwd = run_dir,
        stdout = subprocess.PIPE,
        stderr = subprocess.STDOUT,
        universal_newlines = True
    )
    new_line = None
    while run_proc.poll() is None or new_line != '':
        new_line = run_proc.stdout.readline()
        if new_line != '':
            n_broken_pipes += send_line(new_line, req_obj)

    run_proc.stdout.close()
    return n_broken_pipes


def run_predicts_n_reports(all_dat = None, arg_in = None, req_obj = None):
    lst_cmd = all_dat['step_list']
    # every report and prediction goes in the same dir, with the node number
    # in the file name, the commands run there (cwd) without os.chdir
    path_to_dir = os.path.abspath("predicts_n_reports")
    print("path_to_dir =", path_to_dir)
    try:
        os.mkdir(path_to_dir)

    except FileExistsError:
        print("NOT creating again: ", path_to_dir)

    n_broken_pipes = 0
    for cmd_node in lst_cmd:
        number_str = str(cmd_node['number'])
        new_line = (
            "HTML Report + Reflection Prediction (node " + number_str +
            ") ... START\n"
        )
        n_broken_pipes += send_line(new_line, req_obj)

        # running HTML report generation
        rep_lst_dat_in = ['dials.report']
        for expt_2_add in cmd_node['_lst_expt_out']:
            rep_lst_dat_in.append(expt_2_add)

        for refl_2_add in cmd_node['_lst_refl_out']:
            rep_lst_dat_in.append(refl_2_add)

        output_html_file = "dials.report_n_" + number_str + ".html"
        rep_lst_dat_in.append("output.html=" + output_html_file)
        n_broken_pipes += send_line(str(rep_lst_dat_in) + "\n", req_obj)
        n_broken_pipes += run_n_spit_out(rep_lst_dat_in, path_to_dir, req_obj)

        tmp_html_path = path_to_dir + os.sep + output_html_file
        if os.path.exists(tmp_html_path):
            cmd_node['_html_rep'] = str(tmp_html_path)
            new_line = "HTML Report ready\n"

        else:
            new_line = "No need for HTML report in this step\n"

        n_broken_pipes += send_line(new_line, req_obj)

        # running prediction generation, like CmdNode.generate_predict_n_report
        # but with the node number in the output name (all share one dir)
        if cmd_node['_lst_expt_out'] == []:
            new_line = "No experiments, no Reflection Prediction here\n"
            n_broken_pipes += send_line(new_line, req_obj)

        else:
            pred_lst_dat_in = ['dials.predict']
            for expt_2_add in cmd_node['_lst_expt_out']:
                pred_lst_dat_in.append(expt_2_add)

            output_pred_file = "predicted_n_" + number_str + ".refl"
            pred_lst_dat_in.append("output=" + output_pred_file)
            n_broken_pipes += send_line(str(pred_lst_dat_in) + "\n", req_obj)
            n_broken_pipes += run_n_spit_out(
                pred_lst_dat_in, path_to_dir, req_obj
            )

            tmp_predic_path = path_to_dir + os.sep + output_pred_file
            if os.path.exists(tmp_predic_path):
                cmd_node['_predic_refl'] = str(tmp_predic_path)
                new_line = "Reflection Prediction ready\n"

            else:
                new_line = "No need for Reflection Prediction in this step\n"

            n_broken_pipes += send_line(new_line, req_obj)

        new_line = (
            "HTML Report + Reflection Prediction (node " + number_str +
            ") ... END\n"
        )
        n_broken_pipes += send_line(new_line, req_obj)

    if n_broken_pipes > 0:
        print("<< BrokenPipe err catch >> while sending output")

    all_dat['step_list'] = lst_cmd
    print("\n done \n")
    return all_dat
