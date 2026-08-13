from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
app.secret_key = '1312_change_this_secret_key_in_production'

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- MODÈLES DE BASE DE DONNÉES ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    last_seen = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    questions = db.relationship('Question', backref='author', lazy=True)
    votes = db.relationship('Vote', backref='voter', lazy=True)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    option_red = db.Column(db.String(200), nullable=False)
    option_blue = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    votes = db.relationship('Vote', backref='question', lazy=True, cascade="all, delete-orphan")

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    choice = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)

class GameState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phase = db.Column(db.String(20), default='LOBBY')   # 'LOBBY', 'PROPOSE', 'VOTE'
    phase1_duration = db.Column(db.Integer, default=180) # Durée proposition (sec)
    vote_duration = db.Column(db.Integer, default=20)     # Durée vote (sec)
    results_duration = db.Column(db.Integer, default=10)  # Durée résultats (sec)
    phase_start_time = db.Column(db.DateTime, nullable=True)


def get_game_state():
    state = GameState.query.first()
    if not state:
        state = GameState(phase='LOBBY')
        db.session.add(state)
        db.session.commit()
    return state

def ensure_utc(dt):
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

with app.app_context():
    db.create_all()


# --- ROUTES DU JEU ---

@app.route('/')
def home():
    db.session.expire_all()

    state = get_game_state()
    now = datetime.now(timezone.utc)

    # 1. Récupération de l'utilisateur
    voter_name = session.get('voter_name')
    if not voter_name:
        return redirect(url_for('join'))

    # Mettre à jour l'activité du joueur
    user = User.query.filter_by(username=voter_name).first()
    if not user:
        user = User(username=voter_name, last_seen=now)
        db.session.add(user)
    else:
        user.last_seen = now
    db.session.commit()

    # --- PHASE : LOBBY ---
    if state.phase == 'LOBBY':
        # On ne garde que les joueurs actifs (ping reçus il y a moins de 10s)
        threshold = now - timedelta(seconds=10)
        active_users = User.query.filter(User.last_seen >= threshold).all()
        players = [u.username for u in active_users]

        return render_template('lobby.html', player_name=voter_name, players=players)

    # --- PHASE : PROPOSE ---
    if state.phase == 'PROPOSE':
        p_start = ensure_utc(state.phase_start_time) or now
        elapsed = (now - p_start).total_seconds()
        p1_remaining = max(0, int(state.phase1_duration - elapsed))

        if p1_remaining <= 0:
            state.phase = 'VOTE'
            state.phase_start_time = now
            db.session.commit()
            return redirect(url_for('home'))

        return render_template('propose.html', time_remaining=p1_remaining, player_name=voter_name)

    # --- PHASE : VOTE ---
    p2_start = ensure_utc(state.phase_start_time) or now
    questions = Question.query.order_by(Question.id.asc()).all()
    total_questions = len(questions)

    if total_questions == 0:
        return render_template('index.html', question=None, end_of_game=True)

    elapsed_p2 = (now - p2_start).total_seconds()
    cycle_duration = state.vote_duration + state.results_duration
    current_index = int(elapsed_p2 // cycle_duration)

    if current_index >= total_questions:
        return render_template('index.html', question=None, end_of_game=True)

    question = questions[current_index]
    time_in_cycle = elapsed_p2 % cycle_duration
    vote_remaining = max(0, int(state.vote_duration - time_in_cycle))
    next_question_remaining = max(0, int(cycle_duration - time_in_cycle))

    red_votes = [v.voter.username for v in question.votes if v.choice == 'red']
    blue_votes = [v.voter.username for v in question.votes if v.choice == 'blue']
    total_votes = len(red_votes) + len(blue_votes)

    red_pct = round((len(red_votes) / total_votes * 100)) if total_votes > 0 else 0
    blue_pct = round((len(blue_votes) / total_votes * 100)) if total_votes > 0 else 0

    user_has_voted = False
    user_choice = None
    if user:
        existing_vote = Vote.query.filter_by(user_id=user.id, question_id=question.id).first()
        if existing_vote:
            user_has_voted = True
            user_choice = existing_vote.choice

    return render_template(
        'index.html',
        question=question,
        voter_name=voter_name,
        end_of_game=False,
        vote_remaining=vote_remaining,
        next_question_remaining=next_question_remaining,
        question_num=current_index + 1,
        total_questions=total_questions,
        red_votes=red_votes,
        blue_votes=blue_votes,
        red_pct=red_pct,
        blue_pct=blue_pct,
        user_has_voted=user_has_voted,
        user_choice=user_choice
    )


@app.route('/join', methods=['GET', 'POST'])
def join():
    if request.method == 'POST':
        voter_name = request.form.get('voter_name', '').strip()
        if voter_name:
            session['voter_name'] = voter_name
            return redirect(url_for('home'))
        else:
            flash('Le pseudo ne peut pas être vide.')

    return render_template('join.html')


@app.route('/leave-lobby')
def leave_lobby():
    session.pop('voter_name', None)
    return redirect(url_for('home'))


@app.route('/propose', methods=['POST'])
def propose():
    voter_name = session.get('voter_name')
    option_red = request.form.get('option_red', '').strip()
    option_blue = request.form.get('option_blue', '').strip()

    if not voter_name:
        return redirect(url_for('join'))

    user = User.query.filter_by(username=voter_name).first()
    if not user:
        user = User(username=voter_name)
        db.session.add(user)
        db.session.commit()

    if option_red and option_blue:
        new_question = Question(option_red=option_red, option_blue=option_blue, author=user)
        db.session.add(new_question)
        db.session.commit()
        flash('Question ajoutée avec succès !', 'success')

    return redirect(url_for('home'))


@app.route('/voter/<int:question_id>', methods=['POST'])
def voter(question_id):
    voter_name = session.get('voter_name')
    choice = request.form.get('choice')

    if not voter_name:
        return redirect(url_for('home'))

    user = User.query.filter_by(username=voter_name).first()
    if not user:
        user = User(username=voter_name)
        db.session.add(user)
        db.session.commit()

    if choice in ['red', 'blue']:
        existing_vote = Vote.query.filter_by(user_id=user.id, question_id=question_id).first()
        if not existing_vote:
            new_vote = Vote(choice=choice, user_id=user.id, question_id=question_id)
            db.session.add(new_vote)
            db.session.commit()

    return redirect(url_for('home'))


@app.route('/api/game-status')
def game_status():
    db.session.expire_all()
    
    state = get_game_state()
    now = datetime.now(timezone.utc)
    
    # Seuls les joueurs vus dans les 10 dernières secondes
    threshold = now - timedelta(seconds=10)
    active_users = User.query.filter(User.last_seen >= threshold).all()
    players = [u.username for u in active_users]
    
    data = {
        'phase': state.phase,
        'players': players,
        'current_index': -1,
        'end_of_game': False,
        'redirect_url': url_for('home')
    }

    if state.phase == 'PROPOSE' and state.phase_start_time:
        p_start = ensure_utc(state.phase_start_time)
        elapsed = (now - p_start).total_seconds()
        data['time_remaining'] = max(0, int(state.phase1_duration - elapsed))

    elif state.phase == 'VOTE' and state.phase_start_time:
        p2_start = ensure_utc(state.phase_start_time)
        elapsed = (now - p2_start).total_seconds()
        cycle = state.vote_duration + state.results_duration
        current_index = int(elapsed // cycle)
        
        questions = Question.query.order_by(Question.id.asc()).all()
        
        if len(questions) == 0 or current_index >= len(questions):
            data['end_of_game'] = True
        else:
            q = questions[current_index]
            red_votes = [v.voter.username for v in q.votes if v.choice == 'red']
            blue_votes = [v.voter.username for v in q.votes if v.choice == 'blue']
            total_votes = len(red_votes) + len(blue_votes)

            data.update({
                'current_index': current_index,
                'question_id': q.id,
                'red_votes': red_votes,
                'blue_votes': blue_votes,
                'red_pct': round((len(red_votes) / total_votes * 100)) if total_votes > 0 else 0,
                'blue_pct': round((len(blue_votes) / total_votes * 100)) if total_votes > 0 else 0,
                'vote_remaining': max(0, int(state.vote_duration - (elapsed % cycle))),
                'next_question_remaining': max(0, int(cycle - (elapsed % cycle)))
            })

    return jsonify(data)


@app.route('/api/ping', methods=['POST'])
def ping():
    voter_name = session.get('voter_name')
    if voter_name:
        user = User.query.filter_by(username=voter_name).first()
        if user:
            user.last_seen = datetime.now(timezone.utc)
            db.session.commit()
    return jsonify({'status': 'ok'})


# --- PANNEAU D'ADMINISTRATION ---

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    state = get_game_state()
    questions = Question.query.all()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_settings':
            state.phase1_duration = int(request.form.get('phase1_duration', 180))
            state.vote_duration = int(request.form.get('vote_duration', 20))
            state.results_duration = int(request.form.get('results_duration', 10))
            db.session.commit()

        elif action == 'start_propose':
            state.phase = 'PROPOSE'
            state.phase_start_time = datetime.now(timezone.utc)
            db.session.commit()

        elif action == 'start_vote':
            state.phase = 'VOTE'
            state.phase_start_time = datetime.now(timezone.utc)
            db.session.commit()

        elif action == 'reset_lobby':
            state.phase = 'LOBBY'
            state.phase_start_time = None
            # On vide les anciennes questions et votes pour la nouvelle partie
            Question.query.delete()
            Vote.query.delete()
            db.session.commit()

        return redirect(url_for('admin'))

    return render_template('admin.html', state=state, questions=questions)


@app.route('/admin/delete-question/<int:question_id>', methods=['POST'])
def delete_question(question_id):
    q = Question.query.get_or_404(question_id)
    db.session.delete(q)
    db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/reset-db', methods=['POST'])
def reset_db():
    db.drop_all()
    db.create_all()
    session.clear()
    return redirect(url_for('admin'))


# --- LANCEMENT SERVEUR ---

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)