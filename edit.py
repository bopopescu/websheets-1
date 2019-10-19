#!/usr/bin/python3

import sys, json, config, re

if __name__ == "__main__":
  db = config.connect()
  cursor = db.cursor()

  # should pass message which is a string
  def internal_error(message):
    cursor.close()  
    db.commit()
    db.close()
    print(json.dumps(message))
    sys.exit(0)

  # should pass response which is an object
  def done(**response):
    cursor.close()   
    db.commit()
    db.close()
    print(json.dumps(response))
    sys.exit(0)

  def owner(slug):
    cursor.execute(
      "select author, action from ws_sheets " +
      "WHERE problem = %s AND action != 'preview' ORDER BY ID DESC LIMIT 1;", [slug])
    result = "false"
    for row in cursor:
      author = row[0]
      action = row[1]
      if action == 'delete': return None
      return author
    return None

  def definition(slug, username):
    for definition, sharing, author, action in config.get_rows(
      "select definition, sharing, author, action from ws_sheets " +
      "WHERE problem = %s AND action != 'preview' ORDER BY ID DESC LIMIT 1;", [slug]):
      if action == 'delete': continue
      if not sharing.startswith('open') and author != username: continue # closed-source
      if sharing=='open-nosol' and author != username and not authinfo['is_super']:
        definition = json.loads(definition)
        if 'choices' in definition:
          definition['choices'] = json.dumps([[x[0], None] for x in json.loads(definition['choices'])])
        if 'answer' in definition:
          definition['answer'] = 'REDACTED'
        if 'source_code' in definition:
          bits = definition['source_code'].split(r'\[')
          for i in range(1, len(bits)):
            if r'\show:' in bits[i]:
              p = bits[i].index(r'\show:')
            elif ']\\' in bits[i]:
              p = bits[i].index(']\\')
            bits[i] = ('\nREDACTED\n' if '\n' in bits[i][:p] else ' REDACTED ') + bits[i][p:]
          definition['source_code'] = r'\['.join(bits)
        definition = json.dumps(definition)
      return definition
    internal_error('Whoa, where did that row go?')

  def list_problems(username):
    result = []

    for problem, action, sharing, author in config.get_rows(
      """SELECT o1.problem, o1.action, o1.sharing, o1.author FROM ws_sheets o1
      INNER JOIN (SELECT problem, MAX(id) AS id FROM ws_sheets WHERE action != 'preview' GROUP BY problem) o2
      ON (o1.problem = o2.problem AND o1.id = o2.id);"""):
      if action == 'delete': continue
      if author != username:
        if sharing == 'draft' or sharing == 'hidden': continue
      result.append([problem, author != username, sharing, author])
    #saner sort, files before folders
    for x in result:
      tmp = x[0].split('/')
      x[0] = [tmp[:-1], tmp[-1]]
    result.sort()
#    print(result)
    for x in result: x[0] = '/'.join(x[0][0]+[x[0][1]])
    return result

  def valid(slug):
    return re.match(r"^([\w-]+/)*[\w-]+$", slug)

  def canedit(slug):
    return owner(slug) in [None, authinfo['username']]
        
  def canread(slug):
    myowner = owner(slug)
    if myowner is None: return False
    if myowner == authinfo['username']: return True
    # ignoring previews, get the latest version
    cursor.execute(
      "select author, action, sharing from ws_sheets " +
      "WHERE problem = %s AND action != 'preview' ORDER BY ID DESC LIMIT 1;", [slug])
    result = "false"
    for row in cursor:
      author = row[0]
      action = row[1]
      sharing = row[2]
      return sharing==None or sharing.startswith('open')
    internal_error('Whoa, where did that row go?')

  def get_setting(user, key):
    for (value,) in config.get_rows(
      "select value from ws_settings " +
      "WHERE user = %s AND keyname = %s;", [user, key]):
      return value

  def set_setting(user, key, value):
    cursor.execute("delete from ws_settings where user = %s AND keyname = %s;", [user, key])
    cursor.execute("insert into ws_settings (user, keyname, value)" +
                                        " VALUES (%s, %s, %s)",
                                        (user, key, value))
      
  # start of request handling
  request = json.loads("".join(sys.stdin))

  authinfo = request['authinfo']
  problem = request['problem'] if 'problem' in request else None
  action = request['action']  
  if not authinfo["logged_in"] and action not in ['listmine', 'load']:
    internal_error("Only logged-in users can edit")

  if action not in ['listmine', 'settings', 'showgrades'] and not valid(problem):
    if (action == 'load'):
      done(success=False, message="Requested name does not have valid format: <tt>" + problem + "</tt>")
    else:
      internal_error("Does not have valid format: " + problem)

  def notify(problemname):
    for email_address in config.config_jo['notify_new']:
      import smtplib
      from email.mime.text import MIMEText
      folder = "/".join(problemname.split("/")[:-1])
      slug = problemname.split("/")[-1]
      msg = MIMEText("A new websheet " + problemname + " has been created by " + authinfo['username'] + " at " + config.config_jo["baseurl"] + "/?folder="+folder+"&start="+slug)
      msg['Subject'] = 'New Websheet ' + problemname
      msg['From'] = config.config_jo['notify_from']
      msg['To'] = email_address
      try:
        s = smtplib.SMTP('localhost')
        s.send_message(msg)
        s.quit()
      except Exception as e:
        done(success=False, message="Mail error: "+type(e).__name__+": "+str(e)+"\n[You can set notify_new to an empty list to forcibly silence this error]")

  if (action in ['preview', 'save', 'delete']):
    if not canedit(problem):
      internal_error("You don't have edit permissions for " + problem)
    if action == 'delete':
      sharing = None
      definition = None
    else:
      definition = json.loads(request['definition'])
      sharing = 'open-nosol'
      if 'sharing' in definition:
        sharing = definition['sharing']
    # add a row

    if action == 'save' and owner(problem) == None:
      notify(problem)

    cursor.execute("insert into ws_sheets (author, problem, definition, action, sharing)" +
                   " VALUES (%s, %s, %s, %s, %s)",
                   (authinfo['username'], problem, request['definition'], action, sharing))
    done(success=True, message=action + " of " + problem + " successful.")

      
  if (action in ['rename', 'copy', 'chown']):
    if action == 'chown':
      if not authinfo['is_super']:
        internal_error("You're not a super user.")      
    if action == 'copy' and not canread(problem):
      internal_error("You don't have read permissions for " + problem)
    if action == 'rename' and not canedit(problem):
      internal_error("You don't have edit permissions for " + problem)
    if action != 'chown':
      newname = request['newname']
      if not valid(newname):
        done(success=False, message="New name does not have valid format: " + newname)
      if owner(newname) != None:
        done(success=False, message="There is already a websheet with this name: " + newname)
    
    definition = json.loads(request['definition'])
    sharing = 'open-nosol'
    if 'sharing' in definition:
      sharing = definition['sharing']

    if action == 'copy':
      if 'remarks' not in definition: definition['remarks'] = ""
      definition['remarks'] = "Copied from problem " + problem + " (author: " + owner(problem) + ")\n" + definition['remarks']

    if action != 'chown':
      notify(newname)
      cursor.execute("insert into ws_sheets (author, problem, definition, action, sharing)" +
                     " VALUES (%s, %s, %s, %s, %s)",
                     (authinfo['username'], newname, json.dumps(definition), 'save', sharing))

      if action == 'rename':      
        cursor.execute("insert into ws_sheets (author, problem, action)" +
                       " VALUES (%s, %s, %s)",
                       (authinfo['username'], problem, 'delete'))
      
      done(success=True, message=action + " of " + problem + " to " + newname + " successful.")

    else: # chown
      cursor.execute("insert into ws_sheets (author, problem, definition, action, sharing)" +
                     " VALUES (%s, %s, %s, %s, %s)",
                     (request['newauthor'], problem, json.dumps(definition), 'save', sharing))

      done(success=True, message=action + " of " + problem + " to " + request['newauthor'] + " successful.")
      

  if action == 'load':
    myowner = owner(problem)
    # if it doesn't exist, everything is good
    if myowner == None:
      if authinfo['logged_in']:
        done(success=True, message="Loaded " + problem, new=True, canedit=True, author=authinfo['username'])
      else:
        done(success=False, message=problem + " does not exist. You need to log in to create new problems.")
    # it exists
    if not canread(problem):
      done(success=False, message="You do not have read permissions for: " + problem)
    done(success=True, message="Loaded " + problem, new=False, canedit=myowner == authinfo['username'],
          definition= definition(problem, authinfo['username']), author=myowner)

  if action == 'listmine':
    done(problems = list_problems(authinfo['username']))

  if action == 'settings':
    if 'instructor' in request:
      set_setting(authinfo['username'], 'instructor', request['instructor'])
    inst = get_setting(authinfo['username'], 'instructor')
    if inst is None: inst = ""
    done(success=True, settings={"instructor":inst})
    
  if action == 'showgrades':
    result = {}
    for (student,) in config.get_rows(
      "select user from ws_settings " +
      "WHERE value = %s AND keyname = 'instructor';", [authinfo['username']]):
      stuinfo = {}
      for (passed, time, problem) in config.get_rows(
        "SELECT passed, time, problem from ws_history where user = %s order by id asc;", [student]):
        if (problem not in stuinfo): prev = (False, 0)
        else: prev = stuinfo[problem]
        if not prev[0]: # if not yet passed
          if passed==1:
            curr = (True, prev[1]+1, time.strftime('%Y-%m-%d %H:%M:%S'))
          else:
            curr = (False, prev[1]+1)
          stuinfo[problem] = curr
      result[student] = stuinfo
    done(success=True, grades=result)
    
  internal_error('Unknown action ' + action)
