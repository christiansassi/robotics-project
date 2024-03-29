//
// Created by utente on 19/12/2022.
//
#include "Eigen/Dense"
#include "kinematics.h"
//#include "motionPlan.h"
#include <iostream>
#include <cmath>
#include <cstring>
#include <iostream>

#include "ros/ros.h"

using namespace Eigen;

typedef Matrix<double,Dynamic,6> Matrix6d;
typedef Matrix<double, 1, 6> RowVector6d;
typedef Matrix<double, 1, 8> RowVector8d;
typedef Matrix<double, Dynamic, 8> Matrix8d;

/**
 * @brief converts euler angles to a rotation matrix
 * 
 * @param[in] angles
 * @return Matrix3d
 */

Matrix3d eul2rotm(Vector3d angles){
    double phi = angles(0);
    double theta = angles(1);
    double gamma = angles(2);

    Matrix3d R {
            {cos(phi)*cos(theta), cos(phi)*sin(theta)*sin(gamma) - sin(phi)*cos(gamma), cos(phi)*sin(theta)*cos(gamma) + sin(phi)*sin(gamma)},
            {sin(phi)*cos(theta), sin(phi)*sin(theta)*sin(gamma) + cos(phi)*cos(gamma), sin(phi)*sin(theta)*cos(gamma) - cos(phi)*sin(gamma)},
            {-sin(theta), cos(theta)*sin(gamma), cos(theta)*cos(gamma)}
    };

    return R;
}

/**
 * @brief converts a rotation matrix to euler angles
 * 
 * @param[in] R
 * @return Vector3d
 */

Vector3d rotm2eul(Matrix3d R){
    double x = atan2(R(1,0),R(0,0));
    double y = atan2(-R(2,0), sqrt(pow(R(2,1), 2) + pow(R(2,2), 2)));
    double z = atan2(R(2,1), R(2,2));
    Vector3d vet {{x, y, z}};
    return vet;
}

/**
 * @brief compute the joints to close the gripper
 * 
 * @param[in] Th
 */

void closeGripper(Matrix8d& Th){
    RowVector8d th = Th.row(Th.rows()-1);
    double start = -0.3;
    for (int i=0;i<50;i++){
        th(6) = start+0.01;
        th(7) = start+0.01;
        Th.conservativeResize(Th.rows()+1, Eigen::NoChange );
        Th.row(Th.rows()-1) = th;
    }
}

/**
 * @brief compute the joints to open the gripper
 * 
 * @param[in] Th
 */

void openGripper(Matrix8d& Th){
    RowVector8d th = Th.row(Th.rows()-1);
    double start = 0.3;
    for (int i=0;i<50;i++){
        th(6) = start-0.01;
        th(7) = start-0.01;
        Th.conservativeResize(Th.rows()+1, Eigen::NoChange );
        Th.row(Th.rows()-1) = th;
    }
}

/**
 * @brief computes a path of points between a starting point and a final point.

 * 
 * @param[in] qEs current joint configuration
 * @param[in] xEf final position to reach
 * @param[in] phiEf orientation matrix of the final position
 * @param[in] minT minimum time of the move
 * @param[in] maxT max time of the move
 * @param[in] Th joint matrix passed as reference to store the computed joint configurations
 * @param[in] slow parameter to perform slower moves
 * @return bool
 */

bool p2pMotionPlan(RowVector6d qEs, Vector3d xEf, Vector3d phiEf, double minT, double maxT, Matrix8d& Th, bool slow=false){
    double grip;
    if(Th.size() == 0)
    {
        grip = 0.3;
    }
    else
    {
        grip = Th.row(Th.rows()-1)(6);
    }

    double dt = 0.01;
    if (slow){
        dt = dt/2;
    }
    Matrix6d qEf;
    inverse_kinematics(xEf, qEf, eul2rotm(phiEf));
    bool res = bestInverse(qEs, qEf);
    if (not res)
        return false;

    for (int j=0;j<qEf.rows();j++){
        bool error = false;
        Matrix<double,Dynamic,4> A;
        for (int i=0;i<qEs.size();i++){
            Matrix4d M {
                    {1, minT, pow(minT,2), pow(minT,3)},
                    {0,1, 2*minT, 3*pow(minT,2)},
                    {1, maxT, pow(maxT,2), pow(maxT,3)},
                    {0,1, 2*maxT, 3*pow(maxT,2)}
            };
            Vector4d b {{qEs(i), 0, qEf(j,i), 0}};
            Vector4d a = M.inverse()*b;

            A.conservativeResize(A.rows()+1, Eigen::NoChange );
            A.row(A.rows()-1) = a.transpose();
        }
        double t = minT;
        while (t+0.000001 < maxT){
            RowVector6d th;
            for (int i=0;i < qEs.size(); i++){
                double q = A(i,0) + A(i,1)*t + A(i,2)*t*t + A(i,3)*t*t*t;
                th(i) = q;
            }

            if (not check_angles(th)){
                std::cout << "ERRORE TRAIETTORIA " << t << std::endl;
                error = true;
                break;
            }
            RowVector8d th8;
            th8 << th, grip, grip;

            Th.conservativeResize(Th.rows()+1, Eigen::NoChange );
            Th.row(Th.rows()-1) = th8;

            t += dt;
        }
        if (not error){
            return true;
        }
    }
    return false;
}

/**
 * @brief build an entire movement from a starting point to a final point. It joins three different p2p motionplans
 * 
 * @param[in] qEs current joint configuration
 * @param[in] xEf final position to reach
 * @param[in] phiEf orientation matrix of the final position
 * @param[in] minT minimum time of the move
 * @param[in] maxT max time of the move
 * @param[in] Th joint matrix passed as reference to store the computed joint configurations
 * @return bool
 */

bool threep2p(RowVector6d qEs, Vector3d xEf, Vector3d phiEf, double minT, double maxT, Matrix8d& Th){

    int moveT = (int)((maxT-minT)/3);
    RowVector3d t0 {{minT, minT+moveT, minT+2*moveT}};
    RowVector3d tf {{maxT-2*moveT, maxT-moveT, maxT}};

    //FIRST MOVE
    Vector3d xE1;
    Matrix3d phi;
    direct_kinematics(qEs, phi, xE1);
    xE1(1) = -xE1(1);
    xE1(2) = -0.5;
    RowVector3d phiE1 {{0,0,0}};

    bool res = p2pMotionPlan(qEs, xE1, phiE1, t0(0), tf(0), Th);

    if (not res){
        return false;
    }

    //SECOND MOVE
    qEs = Th.row(Th.rows()-1).block<1,6>(0,0);
    RowVector3d xE2 = xEf;
    xE2(2) = -0.5;
    double sogliaX = 0.3;
    bool slow = false;
    if (abs(xE1(0) - xE2(0)) > sogliaX)
        slow = true;

    res = p2pMotionPlan(qEs, xE2, phiEf, t0(1), tf(1), Th, slow);
    if (not res){
        return false;
    }

    //THIRD MOVE
    qEs = Th.row(Th.rows()-1).block<1,6>(0,0);
    res = p2pMotionPlan(qEs, xEf, phiEf, t0(2), tf(2), Th);
    if (not res){
        return false;
    }

    return true;
}

/**
 * @brief perform a rotation movement
 * 
 * @param[in] qEs current joint configuration
 * @param[in] xEf final position to reach
 * @param[in] type_rot defines the type of the rotation
 * @param[in] minT minimum time of the move
 * @param[in] maxT max time of the move
 * @param[in] Th joint matrix passed as reference to store the computed joint configurations
 * @param[in] recursive1
 * @param[in] recursive2
 * @return bool
 */

bool rotate(RowVector6d qEs, Vector3d xEf, int type_rot, double minT, double maxT, Matrix8d& Th, bool recursive1 = false, bool recursive2 = false){
    Vector3d xRot {-0.277, 0.35, -0.83};
    Vector3d xWork {-0.4, 0.35, -0.74};
    Vector3d xWorkHigh {-0.4, 0.35, -0.5};

    Eigen::RowVector3d dir {{M_PI_2, 0, -M_PI_2}};
    Eigen::RowVector3d phiE0;
    bool cond1, cond2;

    switch (type_rot)
    {
    case 0:
    case 1:
        phiE0 << 0,0,0;
        break;
    case 2:
        phiE0 << M_PI,0,0;
        break;
    case 3:
        phiE0 << -M_PI_2,0,0;
        break;
    case 4:
        phiE0 << M_PI_2,0,0;
        break;
    
    case 5:
        cond1 = rotate(qEs, xEf, 2, minT, maxT, Th, true, false);
        qEs = Th.row(Th.rows() - 1).block<1, 6>(0, 0);
        cond2 = rotate(qEs, xWork, 2, minT, maxT, Th, false, true);
        return cond1 and cond2;
        break;
    
    case 6:
        cond1 = rotate(qEs, xEf, 2, minT, maxT, Th, true, false);
        qEs = Th.row(Th.rows() - 1).block<1, 6>(0, 0);
        cond2 = rotate(qEs, xWork, 1, minT, maxT, Th, false, true);
        return cond1 and cond2;
        break;

    case 7:
        cond1 = rotate(qEs, xEf, 3, minT, maxT, Th, true, false);
        qEs = Th.row(Th.rows() - 1).block<1, 6>(0, 0);
        cond2 = rotate(qEs, xWork, 2, minT, maxT, Th, false, true);
        return cond1 and cond2;
        break;

    case 8:
        cond1 = rotate(qEs, xEf, 3, minT, maxT, Th, true, false);
        qEs = Th.row(Th.rows() - 1).block<1, 6>(0, 0);
        cond2 = rotate(qEs, xWork, 1, minT, maxT, Th, false, true);
        return cond1 and cond2;
        break;

    case 9:
        cond1 = rotate(qEs, xEf, 1, minT, maxT, Th, true, false);
        qEs = Th.row(Th.rows() - 1).block<1, 6>(0, 0);
        cond2 = rotate(qEs, xWork, 4, minT, maxT, Th, false, true);
        return cond1 and cond2;
        break;

    case 10:
        cond1 = rotate(qEs, xEf, 3, minT, maxT, Th, true, false);
        qEs = Th.row(Th.rows() - 1).block<1, 6>(0, 0);
        cond2 = rotate(qEs, xWork, 4, minT, maxT, Th, false, true);
        return cond1 and cond2;
        break;

    default:
        return false;
    }


    bool res = threep2p(qEs, xEf, phiE0, minT, maxT, Th); //from where i am the object
    if (not res)
        return false;
    // CLOSE GRIPPER
    closeGripper(Th);

    qEs = Th.row(Th.rows()-1).block<1,6>(0,0);

    if (!recursive2){

        bool res[2] = {true, true};
        Vector3d xtemp = xEf;
        xtemp(2) = -0.5;
        res[0] = p2pMotionPlan(qEs, xtemp, phiE0, 0, maxT/2, Th);

        qEs = Th.row(Th.rows() - 1).block<1, 6>(0, 0);
        res[1] = p2pMotionPlan(qEs, xWorkHigh, phiE0, 0, maxT, Th);

        if (!(res[0] and res[1]))
            return false;
        
        qEs = Th.row(Th.rows()-1).block<1,6>(0,0);
    }

    res = threep2p(qEs, xRot, dir, minT, maxT, Th);  // rotate object
    if (not res)
        return false;
    // OPEN GRIPPER
    openGripper(Th);

    qEs = Th.row(Th.rows()-1).block<1,6>(0,0);
    phiE0 << M_PI_2,0,0;

    if (!recursive1){
        res = threep2p(qEs, xWork, phiE0, minT, maxT, Th); // takes the object from above
        if (not res)
            return false;
        // CLOSE GRIPPER
        closeGripper(Th);
    }

    return true;
}