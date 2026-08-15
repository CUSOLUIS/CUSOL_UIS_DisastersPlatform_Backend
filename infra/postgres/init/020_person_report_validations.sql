-- CHG-073 — Validaciones de datos de la persona TAMBIÉN en la base.
-- Nacionalidad contra una tabla de referencia (FK), sexo con lista
-- cerrada y fecha de nacimiento plausible. NOT VALID: las filas
-- históricas no bloquean la migración; toda fila nueva sí se valida.
-- El tipo de documento viaja CIFRADO (document_type_encrypted), así
-- que su lista cerrada se impone en el backend, no aquí.

CREATE TABLE IF NOT EXISTS disaster_service.nationalities (
    name TEXT PRIMARY KEY
);

INSERT INTO disaster_service.nationalities (name) VALUES
    ('Afgana'),
    ('Albanesa'),
    ('Alemana'),
    ('Andorrana'),
    ('Angoleña'),
    ('Antiguana'),
    ('Argelina'),
    ('Argentina'),
    ('Armenia'),
    ('Australiana'),
    ('Austríaca'),
    ('Azerbaiyana'),
    ('Bahameña'),
    ('Bahreiní'),
    ('Bangladesí'),
    ('Barbadense'),
    ('Beliceña'),
    ('Beninesa'),
    ('Bielorrusa'),
    ('Birmana'),
    ('Boliviana'),
    ('Bosnia'),
    ('Botsuana'),
    ('Brasileña'),
    ('Bruneana'),
    ('Búlgara'),
    ('Burkinesa'),
    ('Burundesa'),
    ('Butanesa'),
    ('Caboverdiana'),
    ('Camboyana'),
    ('Camerunesa'),
    ('Canadiense'),
    ('Catarí'),
    ('Chadiana'),
    ('Checa'),
    ('Chilena'),
    ('China'),
    ('Chipriota'),
    ('Colombiana'),
    ('Comorense'),
    ('Congoleña'),
    ('Costarricense'),
    ('Croata'),
    ('Cubana'),
    ('Danesa'),
    ('Dominicana'),
    ('Dominiquesa'),
    ('Ecuatoguineana'),
    ('Ecuatoriana'),
    ('Egipcia'),
    ('Emiratí'),
    ('Eritrea'),
    ('Eslovaca'),
    ('Eslovena'),
    ('Española'),
    ('Estadounidense'),
    ('Estonia'),
    ('Etíope'),
    ('Filipina'),
    ('Finlandesa'),
    ('Fiyiana'),
    ('Francesa'),
    ('Gabonesa'),
    ('Gambiana'),
    ('Georgiana'),
    ('Ghanesa'),
    ('Granadina'),
    ('Griega'),
    ('Guatemalteca'),
    ('Guineana'),
    ('Guineana de Guinea-Bisáu'),
    ('Guyanesa'),
    ('Haitiana'),
    ('Hondureña'),
    ('Húngara'),
    ('India'),
    ('Indonesia'),
    ('Iraní'),
    ('Iraquí'),
    ('Irlandesa'),
    ('Islandesa'),
    ('Israelí'),
    ('Italiana'),
    ('Jamaiquina'),
    ('Japonesa'),
    ('Jordana'),
    ('Kazaja'),
    ('Keniana'),
    ('Kirguisa'),
    ('Kiribatiana'),
    ('Kuwaití'),
    ('Laosiana'),
    ('Lesotense'),
    ('Letona'),
    ('Libanesa'),
    ('Liberiana'),
    ('Libia'),
    ('Liechtensteiniana'),
    ('Lituana'),
    ('Luxemburguesa'),
    ('Macedonia'),
    ('Malasia'),
    ('Malauí'),
    ('Maldiva'),
    ('Maliense'),
    ('Maltesa'),
    ('Marfileña'),
    ('Marroquí'),
    ('Marshalesa'),
    ('Mauriciana'),
    ('Mauritana'),
    ('Mexicana'),
    ('Micronesia'),
    ('Moldava'),
    ('Monegasca'),
    ('Mongola'),
    ('Montenegrina'),
    ('Mozambiqueña'),
    ('Namibia'),
    ('Nauruana'),
    ('Nepalí'),
    ('Neerlandesa'),
    ('Neozelandesa'),
    ('Nicaragüense'),
    ('Nigerina'),
    ('Nigeriana'),
    ('Noruega'),
    ('Omaní'),
    ('Pakistaní'),
    ('Palauana'),
    ('Palestina'),
    ('Panameña'),
    ('Papú'),
    ('Paraguaya'),
    ('Peruana'),
    ('Polaca'),
    ('Portuguesa'),
    ('Puertorriqueña'),
    ('Británica'),
    ('Ruandesa'),
    ('Rumana'),
    ('Rusa'),
    ('Salomonense'),
    ('Salvadoreña'),
    ('Samoana'),
    ('Sancristobaleña'),
    ('Sanmarinense'),
    ('Santaluciana'),
    ('Santotomense'),
    ('Sanvicentina'),
    ('Saudí'),
    ('Senegalesa'),
    ('Serbia'),
    ('Seychellense'),
    ('Sierraleonesa'),
    ('Singapurense'),
    ('Siria'),
    ('Somalí'),
    ('Srilanquesa'),
    ('Suazi'),
    ('Sudafricana'),
    ('Sudanesa'),
    ('Sursudanesa'),
    ('Sueca'),
    ('Suiza'),
    ('Surinamesa'),
    ('Tailandesa'),
    ('Tanzana'),
    ('Tayika'),
    ('Timorense'),
    ('Togolesa'),
    ('Tongana'),
    ('Trinitense'),
    ('Tunecina'),
    ('Turcomana'),
    ('Turca'),
    ('Tuvaluana'),
    ('Ucraniana'),
    ('Ugandesa'),
    ('Uruguaya'),
    ('Uzbeka'),
    ('Vanuatuense'),
    ('Vaticana'),
    ('Venezolana'),
    ('Vietnamita'),
    ('Yemení'),
    ('Yibutiana'),
    ('Zambiana'),
    ('Zimbabuense'),
    ('Apátrida'),
    ('Otra')
ON CONFLICT (name) DO NOTHING;

DO $$
BEGIN
    ALTER TABLE disaster_service.missing_person_reports
        ADD CONSTRAINT missing_person_nationality_fk
        FOREIGN KEY (nationality)
        REFERENCES disaster_service.nationalities(name)
        NOT VALID;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE disaster_service.missing_person_reports
        ADD CONSTRAINT missing_person_sex_valid
        CHECK (
            gender_identity IS NULL
            OR gender_identity IN ('Hombre', 'Mujer')
        )
        NOT VALID;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE disaster_service.missing_person_reports
        ADD CONSTRAINT missing_person_birth_date_plausible
        CHECK (
            birth_date IS NULL
            OR (
                birth_date >= DATE '1900-01-01'
                AND birth_date <= CURRENT_DATE
            )
        )
        NOT VALID;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
