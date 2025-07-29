# Force Push Secret Scanner

Ye tool GitHub mein force push events se bane dangling (dereferenced) commits mein secrets ko scan karta hai. Ek [force push](https://git-scm.com/docs/git-push#Documentation/git-push.txt---force) tab hota hai jab developers commit history ko overwrite kar dete hain, jo aksar galti se hard-coded credentials jaise mistakes contain karti hai. Ye project [GHArchive](https://www.gharchive.org/) se archived force push event data use karta hai relevant commits identify karne ke liye.

![Force Push Secret Scanner Demo](./demo.gif)

Ye project [Sharon Brizinov](https://github.com/SharonBrizinov) ke collaboration mein banaya gaya hai. Please [Sharon ka blog post](https://trufflesecurity.com/blog/guest-post-how-i-scanned-all-of-github-s-oops-commits-for-leaked-secrets) padhiye yeh janne ke liye ki unhone GH Archive dataset mein force push commits kaise identify kiye aur $25k bounties kaise kamaye.

## Quickstart (recommended)

1. Force Push Commits SQLite DB (`force_push_commits.sqlite3`) download karo Google Form submission ke through: <https://forms.gle/344GbP6WrJ1fhW2A6>. Ye aapko locally kisi bhi user/org ke force push commits search karne deta hai.

2. Python dependencies install karo:

```bash
pip install -r requirements.txt
```
3. Kisi org/user ko secrets ke liye scan karo:

```bash
python force_push_scanner.py <org> --db-file /path/to/force_push_commits.sqlite3 --scan
```

### Alternative Usage: BigQuery

Agar aap khud BigQuery query karna prefer karte hain, to aap hamara public table use kar sakte hain jo GHArchive dataset pe based hai (queries typically Google account ke saath free hoti hain). 

```sql
SELECT *
FROM `external-truffle-security-gha.force_push_commits.pushes`
WHERE repo_org = '<ORG>';
```

Results ko CSV mein export karo, phir scanner run karo:

```bash
python force_push_scanner.py <org> --events-file /path/to/force_push_commits.csv --scan
```

---

## Script kya karta hai

* `<org>` ke liye zero-commit **force-push events** list karta hai.
* Har repo ke stats print karta hai.
* (Optional `--scan`) Har commit ke liye:
  * Overwritten commits identify karta hai.
  * Overwritten commits pe **TruffleHog** (`--only-verified`) run karta hai.
  * Verified findings ko commit link ke saath output karta hai.

---

## Command-line options (abridged)

Full help ke liye `python force_push_scanner.py -h` run karo.

* `--db-file`     SQLite DB path (preferred)
* `--events-file` CSV export path (BigQuery)
* `--scan`        TruffleHog scanning enable karo
* `--verbose`, `-v` Debug logging

---

## FAQs

### Force Push kya hai?

Force push (`git push --force`) remote branch pointer ko exactly wahan move kar deta hai jahan aapka local branch pointer hai, chahe iska matlab ye ho ki remote branch mein ab woh commits nahi hain jo pehle its history mein the. Ye basically remote se kehta hai ki apni purani history bhool jao aur meri history use karo. Jo commits sirf remote ki purani history se reachable the, woh ab repository mein unreachable ho jaate hain (inhe "dangling commits" kehte hain). Aapka local Git eventually inhe clean kar sakta hai, lekin remote services like GitHub inhe thoda aur time tak rakhti hain apne rules ke according. Ye action aksar tab kiya jata hai jab developer galti se koi data commit kar deta hai jisme mistake ho, jaise hard-coded credentials. More details ke liye [Sharon ka blog post](https://trufflesecurity.com/blog/guest-post-how-i-scanned-all-of-github-s-oops-commits-for-leaked-secrets) aur git ka documentation on [force pushes](https://git-scm.com/docs/git-push#Documentation/git-push.txt---force) dekhiye.

### Kya ye dataset GitHub ke *sabhi* Force Push events contain karta hai?

**tl;dr:** Nahi. Ye dataset specifically **Zero-Commit Force Push Events** pe focus karta hai, jo hamara believe hai ki sabse likely cases hain jahan secrets accidentally push hue aur phir unhe remove karne ki koshish ki gayi.

#### Sirf Zero-Commit Force Pushes pe focus kyon?

1. **Zero-Commit Force Pushes aksar secret removal indicate karte hain**  
   Developers jo galti se secrets push kar dete hain, woh frequently apni history ko mistake se pehle ke point pe reset kar dete hain, phir exposed data remove karne ke liye force push karte hain. Ye types ke pushes typically push events ke roop mein show hote hain jo `HEAD` modify karte hain lekin zero commits contain karte hain. Hamari research indicate karti hai ki ye pattern strongly correlated hai sensitive content delete karne ki attempts ke saath.

2. **Saare Force Pushes GH Archive se alone detectable nahi hain**  
   Force push ek low-level git operation hai jo commonly many workflows mein use hoti hai, including rebasing aur branches clean karna. Har type ke force push identify karne ke liye har repository clone karni padegi aur uski git history inspect karni padegi. Ye approach GitHub ke scale pe practical nahi hai aur is project ke scope se bahar hai.

#### Force Push ka kya example hai jo include nahi hai?

Ek scenario consider karo jahan developer ek secret push karta hai, phir mistake realize karta hai aur branch ko earlier state pe reset kar deta hai. Agar woh force pushing se pehle naye, clean commits add karta hai, to resulting `PushEvent` mein ek ya zyada commits honge. Ye example capture nahi hoga kyunki hamara dataset sirf zero commits wale push events include karta hai.

### GHArchive kya hai?

GH Archive *sabhi* public GitHub activity ka ek public dataset hai. Ye security researchers aur developers ke liye ek great resource hai GitHub ecosystem ki security landscape analyze aur understand karne ke liye. Ye BigQuery pe publicly available hai, lekin pura dataset query karna expensive hai ($170/query). Humne GH Archive dataset ko trim kiya hai sirf force push commits include karne ke liye.

### Force Push Commits DB publicly host kyon nahi karte?

Hum large downloads ko form ke peeche gate karte hain abuse deter karne ke liye; public BigQuery dataset sabhi ke liye open hai.

### Dataset Updates

SQLite3 Database aur BigQuery Table har din 2 PM EST pe previous day ke data ke saath update hote hain. 

---

Ye repository *as-is* provide kiya gaya hai; hum PRs ko time permit karne pe review karenge.

**Disclaimer**: Ye tool exclusively authorized defensive security operations ke liye intended hai. Hamesha koi bhi analysis perform karne se pehle explicit permission lo, kabhi bhi aisi data access ya download mat karo jiske liye aap authorized nahi hain, aur koi bhi unauthorized ya malicious use strictly prohibited hai aur aapke apne risk pe hai.
